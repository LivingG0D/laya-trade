"""FP8 (8-bit, e4m3) quantization + CUDA-graph inference for the Laya DecisionModel.

Needs an FP8 tensor-core GPU (sm_89 Ada / sm_90 Hopper+) and torch>=2.4 (`torch._scaled_mm`).

Why not bitsandbytes int8: at Laya's batch size (1 row, ~130 tokens) inference is
kernel-launch bound, so LLM.int8()'s extra kernels made it 3x slower than fp32. FP8 plus
CUDA-graph replay removes the launch overhead, so it is faster than fp32 and uses less VRAM.

Only the ModernBERT encoder's nn.Linear layers become FP8 (~87% of the weights). The
decision head stays bf16: MultiheadAttention reads `in_proj_weight` directly.

    python -m src.laya_fp8                           # quantize + parity/latency check
    agent = load_fp8("models/laya_crypto_fp8")       # load in the engine
"""
import json
import shutil
import threading
import time
from pathlib import Path

import torch
from torch import nn

QUANT_MARKER = "quant_config.json"
FP8 = torch.float8_e4m3fn
FP8_MAX = torch.finfo(FP8).max
SEQ_BUCKET = 64  # pad sequence length to a multiple of this so a few graphs cover all prompts


class FP8Linear(nn.Module):
    """y = (x_fp8 @ W_fp8^T) * x_scale * w_scale_row + b. Per-tensor activation, per-row weight scale."""

    def __init__(self, in_features: int, out_features: int, bias: bool):
        super().__init__()
        # uint8 storage: .to(dtype) leaves integer buffers alone, so model.to(bf16) can't upcast it.
        self.register_buffer("weight_fp8", torch.zeros(out_features, in_features, dtype=torch.uint8))
        self.register_buffer("weight_scale", torch.ones(out_features))
        self.bias = nn.Parameter(torch.zeros(out_features), requires_grad=False) if bias else None

    @classmethod
    def from_linear(cls, lin: nn.Linear) -> "FP8Linear":
        q = cls(lin.in_features, lin.out_features, lin.bias is not None)
        w = lin.weight.data.float()
        scale = w.abs().amax(dim=1).clamp(min=1e-12) / FP8_MAX
        q.weight_fp8.copy_((w / scale[:, None]).to(FP8).view(torch.uint8))
        q.weight_scale.copy_(scale)
        if lin.bias is not None:
            q.bias.data.copy_(lin.bias.data)
        return q

    def forward(self, x):
        shape = x.shape
        x2 = x.reshape(-1, shape[-1])
        x_scale = x2.abs().amax().float().clamp(min=1e-12) / FP8_MAX
        xq = (x2 / x_scale).to(FP8)
        y = torch._scaled_mm(xq, self.weight_fp8.view(FP8).t(), scale_a=x_scale,
                             scale_b=torch.ones((), device=x.device), out_dtype=torch.bfloat16)
        y = y * self.weight_scale.to(y.dtype)
        if self.bias is not None:
            y = y + self.bias.to(y.dtype)
        return y.reshape(*shape[:-1], -1)


def _swap_linears(module: nn.Module, placeholder: bool) -> None:
    for name, child in module.named_children():
        if isinstance(child, nn.Linear):
            q = (FP8Linear(child.in_features, child.out_features, child.bias is not None)
                 if placeholder else FP8Linear.from_linear(child))
            setattr(module, name, q)
        else:
            _swap_linears(child, placeholder)


class _GraphedForward:
    """Replays a captured CUDA graph per padded input shape instead of launching ~1000 kernels."""

    def __init__(self, model: nn.Module, pad_id: int):
        self.eager = model.forward
        self.pad_id = pad_id
        self.graphs = {}
        self.pool = torch.cuda.graph_pool_handle()
        self.lock = threading.Lock()  # engine calls predict from worker threads; static buffers are shared

    def _capture(self, args):
        static_in = [a.clone() for a in args]
        side = torch.cuda.Stream()
        side.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(side):
            for _ in range(2):
                self.eager(*static_in)
        torch.cuda.current_stream().wait_stream(side)
        graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph, pool=self.pool):
            static_out = self.eager(*static_in)
        return graph, static_in, static_out

    def __call__(self, input_ids, attention_mask, marker_pos, marker_mask, qtype, detach_encoder=False):
        if not input_ids.is_cuda or torch.is_grad_enabled():
            return self.eager(input_ids, attention_mask, marker_pos, marker_mask, qtype)
        pad = (-input_ids.shape[1]) % SEQ_BUCKET
        if pad:
            input_ids = nn.functional.pad(input_ids, (0, pad), value=self.pad_id)
            attention_mask = nn.functional.pad(attention_mask, (0, pad), value=0)
        args = (input_ids, attention_mask, marker_pos, marker_mask, qtype)
        key = (torch.is_autocast_enabled(), *(tuple(a.shape) for a in args))
        with self.lock:
            if key not in self.graphs:
                self.graphs[key] = self._capture(args)
            graph, static_in, static_out = self.graphs[key]
            for dst, src in zip(static_in, args):
                dst.copy_(src)
            graph.replay()
            return tuple(o.clone() for o in static_out)


def load_fp8(model_dir: str, device: str = "cuda", graphs: bool = True):
    """Load a prequantized FP8 Laya checkpoint (written by `quantize()`) on CUDA."""
    import laya.agent

    if not torch.cuda.is_available() or torch.cuda.get_device_capability() < (8, 9):
        raise RuntimeError("FP8 inference needs an sm_89+ CUDA GPU (RTX 40xx / H100 or newer)")
    orig_build = laya.agent.build_model

    def build_fp8(*args, **kwargs):
        model = orig_build(*args, **kwargs)
        _swap_linears(model.encoder, placeholder=True)
        return model

    # ponytail: module-level patch is not thread-safe; load models one at a time.
    laya.agent.build_model = build_fp8
    try:
        agent = laya.load(model_dir, device=device)
    finally:
        laya.agent.build_model = orig_build
    agent.model.to(torch.bfloat16)  # fp32 placeholders -> bf16; uint8 FP8 weights untouched
    if graphs:
        agent.model.forward = _GraphedForward(agent.model, agent.tok.pad_token_id or 0)
    return agent


def quantize(src: Path, dst: Path) -> None:
    """Write an FP8 checkpoint of the fp32 model at `src` to `dst` (same layout, laya-loadable via load_fp8)."""
    import laya
    from safetensors.torch import save_file

    agent = laya.load(str(src), device="cpu")
    _swap_linears(agent.model.encoder, placeholder=False)  # quantize from the fp32 weights
    agent.model.to(torch.bfloat16)
    dst.mkdir(parents=True, exist_ok=True)
    sd = {k: v.contiguous() for k, v in agent.model.state_dict().items()}
    save_file(sd, str(dst / "model.safetensors"))
    for name in ("rl_agent_config.json", "calibration.json"):
        if (src / name).exists():
            shutil.copy2(src / name, dst / name)
    for sub in ("tokenizer", "encoder"):
        shutil.copytree(src / sub, dst / sub, dirs_exist_ok=True)
    (dst / QUANT_MARKER).write_text(json.dumps(
        {"method": "fp8_e4m3 W8A8 (torch._scaled_mm)", "scope": "encoder nn.Linear",
         "weight_scale": "per-row", "activation_scale": "per-tensor dynamic",
         "source": src.name}, indent=2))


if __name__ == "__main__":
    import warnings

    import laya

    warnings.filterwarnings("ignore", category=RuntimeWarning, module="laya.*")
    root = Path(__file__).resolve().parent.parent / "models"
    src, dst = root / "fine_tuned_laya_crypto", root / "laya_crypto_fp8"
    quantize(src, dst)
    print(f"saved {dst} ({(dst / 'model.safetensors').stat().st_size / 1e6:.0f} MB)")

    q = {"position": {"type": "choice", "instructions": "Determine optimal trading position",
                      "criteria": {"STRONG_LONG": "Clear upward trend", "SCALP_LONG": "Support bounce",
                                   "NEUTRAL_HOLD": "Choppy range", "SCALP_SHORT": "Resistance rejection",
                                   "STRONG_SHORT": "Clear downward breakdown"}}}
    texts = [f"Asset: BTCUSDT.P | Micro TF: 5m | Price: {p} | 1H Macro Trend: {t} | Market Regime: {r} | "
             f"ADX: {adx} | Order Book OBI: {obi} | TTM Squeeze: OFF | Trend State: {s}"
             for p, t, r, adx, obi, s in [
                 (85000.0, "BULLISH", "TREND_UP", 31.0, "+12.0%", "UPTREND"),
                 (84210.5, "BEARISH", "TREND_DOWN", 44.2, "-18.3%", "DOWNTREND"),
                 (85102.25, "NEUTRAL", "CHOP_RANGE", 14.9, "+0.4%", "NEUTRAL"),
                 (61000.0, "BEARISH", "CHOP_RANGE", 22.0, "-3.1%", "NEUTRAL")]]

    def run(agent, n=30):
        probs = [agent.predict(t, q)["answers"]["position"]["probabilities"] for t in texts]
        torch.cuda.synchronize()
        start = time.perf_counter()
        for _ in range(n):
            agent.predict(texts[0], q)
        torch.cuda.synchronize()
        return probs, (time.perf_counter() - start) / n * 1000, torch.cuda.memory_allocated() / 1e6

    ref, ms_ref, mem_ref = run(laya.load(str(src), device="cuda"))
    torch.cuda.empty_cache()
    out, ms_fp8, mem_fp8 = run(load_fp8(str(dst)))

    drift = max(abs(a[k] - b[k]) for a, b in zip(ref, out) for k in a)
    same = sum(max(a, key=a.get) == max(b, key=b.get) for a, b in zip(ref, out))
    print(f"latency  fp32 {ms_ref:.1f} ms -> fp8 {ms_fp8:.1f} ms")
    print(f"VRAM     fp32 {mem_ref:.0f} MB -> fp8 {mem_fp8:.0f} MB (incl. previous model until freed)")
    print(f"accuracy top choice match {same}/{len(texts)}, max prob drift {drift:.4f}")
    assert same == len(texts) and drift < 0.03, "fp8 diverges from fp32"
    assert ms_fp8 < ms_ref, "fp8 slower than fp32"
