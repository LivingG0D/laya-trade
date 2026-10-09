import os
import json
import time
import hashlib
import logging
import asyncio
import warnings
from pathlib import Path
from typing import Dict, Any, List, Optional, Sequence

import threading
import numpy as np

from .config import MODELS_DIR, HF_REPO_ID, DEFAULT_MODEL_FILE

# The model runtimes (onnxruntime, transformers, laya, torch) are imported where
# they are used, so the app and the backtest run on the fallback engine without them.

logger = logging.getLogger("laya_engine")

class LayaEngine:
    def __init__(
        self,
        repo_id: str = HF_REPO_ID,
        model_filename: str = DEFAULT_MODEL_FILE,
        device: Optional[str] = None,
    ):
        self.repo_id = repo_id
        self.model_filename = model_filename
        self.model_path = MODELS_DIR / model_filename
        if device:
            self.device = device
        else:
            try:
                import torch
                self.device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                self.device = "cpu"

        # Model runtimes
        self._ort_session: Optional[Any] = None  # onnxruntime.InferenceSession
        self._tokenizer: Optional[Any] = None
        self._temp: float = 0.7470113039016724
        self._max_len: int = 512
        self._class_weights = np.array([1.46191935, 1.34447984, 0.19360081], dtype=np.float32)
        self._agent: Optional[Any] = None

        self._is_ready = False
        self._load_lock = asyncio.Lock()
        self._infer_lock = threading.Lock()

        # Decision Question Specification matching fine-tuned & INT8 calibrated crypto model
        self._q_spec = {
            "t": "choice",
            "ins": "Determine optimal trade action based on multi-timeframe confluence and microstructure.",
            "crit": {
                "LONG": "Upward expansion hitting +1.2% TP before -0.6% SL within 2 hours",
                "SHORT": "Downward expansion hitting -1.2% TP before +0.6% SL within 2 hours",
                "NO_TRADE": "Sideways chop, stop-loss triggered first, or no barrier hit within 2 hours"
            }
        }
        self._option_keys = ["LONG", "SHORT", "NO_TRADE"]

    @property
    def runtime(self) -> str:
        """The path ``decide`` takes: "onnx", "agent" (native model blended 50/50 with the fallback) or "fallback"."""
        if self._ort_session is not None:
            return "onnx"
        return "agent" if self._agent is not None else "fallback"

    @property
    def is_ready(self) -> bool:
        return self._is_ready and (self._ort_session is not None or self._agent is not None)

    def is_model_downloaded(self) -> bool:
        """Check whether quantized ONNX model, safetensors checkpoint, or GGUF exists."""
        if self.model_path.is_dir():
            for onnx_candidate in ["model_quantized.onnx", "laya.onnx", "model.onnx"]:
                f = self.model_path / onnx_candidate
                if f.exists() and f.stat().st_size > 10_000_000:
                    return True
            st_file = self.model_path / "model.safetensors"
            if st_file.exists() and st_file.stat().st_size > 10_000_000:
                return True
        return self.model_path.exists() and self.model_path.stat().st_size > 10_000_000

    def download_model(self) -> Path:
        """Download model from Hugging Face if not already present."""
        if self.is_model_downloaded():
            logger.info(f"Model already present at {self.model_path}")
            return self.model_path

        logger.info(f"Downloading {self.model_filename} from {self.repo_id}...")
        from huggingface_hub import hf_hub_download

        try:
            downloaded = hf_hub_download(
                repo_id=self.repo_id,
                filename=self.model_filename,
                local_dir=str(MODELS_DIR),
                local_dir_use_symlinks=False
            )
            self.model_path = Path(downloaded)
            logger.info(f"Model successfully downloaded to {self.model_path} ({self.model_path.stat().st_size / 1e6:.1f} MB)")
            return self.model_path
        except Exception as e:
            logger.error(f"Failed to download model from Hugging Face: {e}")
            raise

    async def start_server(self) -> bool:
        """Load static INT8 quantized Laya crypto model or fallback runtime engines."""
        async with self._load_lock:
            # 1. Primary: Static Quantized INT8 ONNX Model (Fast, low-latency CPU/DirectML inference)
            if self.model_path.is_dir():
                onnx_file = None
                for candidate in ["model_quantized.onnx", "laya.onnx", "model.onnx"]:
                    p = self.model_path / candidate
                    if p.exists() and p.stat().st_size > 10_000_000:
                        onnx_file = p
                        break

                if onnx_file is not None:
                    try:
                        import onnxruntime as ort
                        from transformers import AutoTokenizer

                        logger.info(f"Loading Static INT8 Quantized Laya model from {onnx_file}...")
                        tok_path = self.model_path / "tokenizer" if (self.model_path / "tokenizer").exists() else self.model_path
                        self._tokenizer = await asyncio.to_thread(AutoTokenizer.from_pretrained, str(tok_path))

                        # Read calibrated temperature & class weights
                        cfg_file = self.model_path / "rl_agent_config.json"
                        calib_file = self.model_path / "calibration.json"
                        if calib_file.exists():
                            try:
                                with open(calib_file, "r", encoding="utf-8") as f:
                                    calib_data = json.load(f)
                                    if "fitted_temperature" in calib_data:
                                        self._temp = float(calib_data["fitted_temperature"])
                                    if "class_weights" in calib_data:
                                        self._class_weights = np.array(calib_data["class_weights"], dtype=np.float32)
                            except Exception:
                                pass
                        if cfg_file.exists():
                            try:
                                with open(cfg_file, "r", encoding="utf-8") as f:
                                    cfg_data = json.load(f)
                                    t_opt = cfg_data.get("temperature_by_options", {}).get("choice:3-5")
                                    if t_opt is not None:
                                        self._temp = float(t_opt)
                            except Exception:
                                pass

                        # Configure optimized ONNX Runtime session
                        opts = ort.SessionOptions()
                        num_cpus = os.cpu_count() or 4
                        opts.intra_op_num_threads = min(num_cpus, 16)
                        opts.inter_op_num_threads = 2
                        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

                        providers = ["CPUExecutionProvider"]
                        self._ort_session = await asyncio.to_thread(
                            ort.InferenceSession,
                            str(onnx_file),
                            sess_options=opts,
                            providers=providers
                        )
                        self._is_ready = True
                        logger.info(
                            f"Laya Crypto INT8 ONNX model loaded successfully! "
                            f"(threads={opts.intra_op_num_threads}, temp={self._temp:.4f})"
                        )
                        await self._warmup()
                        return True
                    except Exception as e:
                        logger.error(f"Failed to initialize INT8 ONNX session: {e}", exc_info=True)

            # 2. Secondary: PyTorch native fine-tuned checkpoint (safetensors)
            if self.model_path.is_dir() and (self.model_path / "model.safetensors").exists():
                try:
                    import laya
                    logger.info(f"Loading fine-tuned Laya crypto model from {self.model_path} on {self.device}...")
                    warnings.filterwarnings("ignore", category=RuntimeWarning, module="laya.*")
                    loader = laya.load
                    if (self.model_path / "quant_config.json").exists():
                        from .laya_fp8 import load_fp8  # FP8 + CUDA graphs, sm_89+ only
                        loader = load_fp8
                    self._agent = await asyncio.to_thread(
                        loader,
                        str(self.model_path),
                        device=self.device
                    )
                    self._is_ready = True
                    logger.info(f"Fine-tuned Laya crypto model loaded successfully on {self.device}!")
                    asyncio.create_task(self._warmup())
                    return True
                except Exception as e:
                    logger.error(f"Failed to load fine-tuned model with laya.load: {e}", exc_info=True)

            logger.warning("Could not initialize INT8 ONNX or native model. Internal calibrated engine will be used.")
            return False

    async def _warmup(self):
        """Warm up model asynchronously."""
        try:
            if self._ort_session is not None:
                dummy_state = {
                    "symbol": "BTCUSDT.P",
                    "interval": "5m",
                    "price": 85000.0,
                    "rsi": 50.0,
                    "trend_state": "NEUTRAL",
                    "atr_pct": 0.5,
                    "vol_spike": 1.0,
                }
                await asyncio.to_thread(self._run_ort_inference, dummy_state)
                logger.info("Laya Crypto INT8 ONNX model warmup completed.")
            elif self._agent is not None:
                state = "Asset: BTCUSDT.P | Micro TF: 5m | Price: 80000 | 1H Trend: NEUTRAL"
                q = {
                    "position": {
                        "type": "choice",
                        "instructions": "Optimal position?",
                        "criteria": {"A": "A", "B": "B"}
                    }
                }
                await asyncio.to_thread(self._agent.predict, state, q)
                logger.info("Fine-tuned Laya crypto model warmup completed.")
        except Exception as e:
            logger.debug(f"Warmup error: {e}")

    def _run_ort_inference(self, market_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute ONNX Runtime inference using static INT8 quantized weights.
        Returns calibrated trade probabilities, direction, confidence score, and metrics.
        """
        if self._ort_session is None or self._tokenizer is None:
            raise RuntimeError("INT8 ONNX session or tokenizer is not initialized.")

        with self._infer_lock:
            return self._run_ort_inference_locked(market_state)

    def _run_ort_inference_locked(self, market_state: Dict[str, Any]) -> Dict[str, Any]:
        t_start = time.perf_counter()

        from laya.common import build_sequence

        # Format input state matching the training/validation data schema
        trend_state = str(market_state.get("trend_state", "NEUTRAL")).upper()
        htf = market_state.get("htf") or {}
        htf_trend = htf.get("trend", "NEUTRAL")
        # Per-timeframe values from the market snapshot where the state has them.
        mtf = market_state.get("multi_timeframe") or {}
        rsi_15m = (mtf.get("15m") or {}).get("rsi")
        trend_4h = (mtf.get("4h") or {}).get("trend") or htf_trend

        supertrend_dir = "BULLISH" if "BULL" in trend_state else ("BEARISH" if "BEAR" in trend_state else "NEUTRAL")
        confluence_count = 3 if "BULL" in trend_state else (2 if "BEAR" in trend_state else 1)

        raw_time = market_state.get("time", time.time())
        ts_ms = int(raw_time * 1000) if raw_time < 1e11 else int(raw_time)

        state_dict = {
            "symbol": str(market_state.get("symbol", "BTCUSDT.P")).replace(".P", "").replace("-SWAP-", ""),
            "timeframe": str(market_state.get("interval", "5m")),
            "datetime": market_state.get("datetime") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_ms / 1000)),
            "timestamp": ts_ms,
            "close": float(market_state.get("price", 0.0)),
            "rsi_15m": float(rsi_15m if rsi_15m is not None else market_state.get("rsi", 50.0)),
            "supertrend_15m": supertrend_dir,
            "supertrend_dist_pct": round(float(market_state.get("atr_pct", 0.5)), 2),
            "htf_trends": {
                "30m": htf_trend,  # no 30m in the state: the 1h trend stands in
                "1h": htf_trend,
                "4h": trend_4h
            },
            "confluence_bull_count": confluence_count,
            "micro_1m_rsi": float(market_state.get("rsi", 50.0)),
            "volume_surge_ratio": round(float(market_state.get("vol_spike", 1.0)), 2)
        }

        # Build tokenized sequence with marker positions
        seq, markers = build_sequence(
            self._tokenizer,
            state_dict,
            self._q_spec,
            max_len=self._max_len,
            head_max_len=192
        )

        # Pad sequence and attention mask to exact fixed max_length=512 for static INT8 graph
        seq_len = len(seq)
        pad_len = max(0, self._max_len - seq_len)
        padded_ids = (seq + [self._tokenizer.pad_token_id] * pad_len)[:self._max_len]
        padded_att = ([1] * seq_len + [0] * pad_len)[:self._max_len]

        m_pos = list(markers)
        m_mask = [True] * len(m_pos)
        while len(m_pos) < 3:
            m_pos.append(0)
            m_mask.append(False)
        m_pos = m_pos[:3]
        m_mask = m_mask[:3]

        feed_dict = {
            "input_ids": np.expand_dims(np.array(padded_ids, dtype=np.int64), 0),
            "attention_mask": np.expand_dims(np.array(padded_att, dtype=np.int64), 0),
            "marker_pos": np.expand_dims(np.array(m_pos, dtype=np.int64), 0),
            "marker_mask": np.expand_dims(np.array(m_mask, dtype=bool), 0),
            "qtype": np.array([0], dtype=np.int64),
        }

        # Run ONNX inference
        outs = self._ort_session.run(["logits", "act_logits"], feed_dict)
        latency_ms = round((time.perf_counter() - t_start) * 1000, 1)

        logits = outs[0][0, :3]  # [LONG, SHORT, NO_TRADE]

        # Softmax with calibrated temperature scaling and Bayesian prior rebalancing
        temp = max(0.1, self._temp)
        # Class weights from calibration: [1.4619, 1.3445, 0.1936]
        # Rebalancing factor alpha=0.55 compensates for 86% NO_TRADE dataset bias
        alpha = 0.55
        adj_logits = (logits + alpha * np.log(self._class_weights)) / temp
        exp_z = np.exp(adj_logits - np.max(adj_logits))
        probs = exp_z / np.sum(exp_z)

        p_long = float(probs[0])
        p_short = float(probs[1])
        p_no_trade = float(probs[2])

        # Directional margin between LONG and SHORT
        dir_sum = max(1e-5, p_long + p_short)
        dir_ratio = p_long / dir_sum  # 1.0 = 100% Long, 0.0 = 100% Short

        # Decide position
        if p_long > p_short and dir_ratio >= 0.505 and p_long >= p_no_trade * 0.90:
            pos_choice = "STRONG_LONG" if dir_ratio >= 0.54 or p_long >= 0.40 else "SCALP_LONG"
        elif p_short > p_long and dir_ratio <= 0.495 and p_short >= p_no_trade * 0.90:
            pos_choice = "STRONG_SHORT" if dir_ratio <= 0.46 or p_short >= 0.40 else "SCALP_SHORT"
        else:
            pos_choice = "NEUTRAL_HOLD"

        is_long = "LONG" in pos_choice
        is_short = "SHORT" in pos_choice
        directional_conviction = dir_ratio if is_long else ((1.0 - dir_ratio) if is_short else max(dir_ratio, 1.0 - dir_ratio))

        # Distribute into 5 UI bars for TradingView-style visual telemetry
        if is_long:
            p_sl = round(p_long * 0.65, 4) if pos_choice == "STRONG_LONG" else round(p_long * 0.40, 4)
            p_scl = round(p_long - p_sl, 4)
            p_ss = round(p_short * 0.35, 4)
            p_scs = round(p_short - p_ss, 4)
        elif is_short:
            p_ss = round(p_short * 0.65, 4) if pos_choice == "STRONG_SHORT" else round(p_short * 0.40, 4)
            p_scs = round(p_short - p_ss, 4)
            p_sl = round(p_long * 0.35, 4)
            p_scl = round(p_long - p_sl, 4)
        else:
            p_sl = round(p_long * 0.40, 4)
            p_scl = round(p_long * 0.60, 4)
            p_ss = round(p_short * 0.40, 4)
            p_scs = round(p_short * 0.60, 4)
        p_nh = round(p_no_trade, 4)

        prob_dict = {
            "STRONG_LONG": p_sl,
            "SCALP_LONG": p_scl,
            "NEUTRAL_HOLD": p_nh,
            "SCALP_SHORT": p_scs,
            "STRONG_SHORT": p_ss,
        }

        # Confidence calculation based on directional conviction
        if directional_conviction >= 0.60:
            conf_score = 4
        elif directional_conviction >= 0.54:
            conf_score = 3
        elif directional_conviction >= 0.50:
            conf_score = 2
        else:
            conf_score = 1

        is_squeeze = market_state.get("is_squeeze", False)
        vol_spike = market_state.get("vol_spike", 1.0)
        sq_risk = 0.75 if is_squeeze else min(0.9, vol_spike * 0.25)

        time_horizon = "SWING" if "STRONG" in pos_choice else ("SCALP" if "SCALP" in pos_choice else "WAIT")

        return {
            "position": pos_choice,
            "probabilities": prob_dict,
            "long_prob": round(p_long, 4),
            "short_prob": round(p_short, 4),
            "hold_prob": round(p_no_trade, 4),
            "directional_conviction": round(float(directional_conviction), 4),
            "confidence_score": conf_score,
            "confidence_prob": round(float(directional_conviction), 3),
            "squeeze_risk": round(float(sq_risk), 2),
            "time_horizon": time_horizon,
            "latency_ms": latency_ms,
            "engine": "laya_crypto_int8"
        }

    async def decide(self, market_state: Dict[str, Any]) -> Dict[str, Any]:
        """System-1 decision for one market state (see ``decide_batch``)."""
        return (await self.decide_batch([market_state]))[0]

    async def decide_batch(self, market_states: Sequence[Dict[str, Any]],
                           agent_cache: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Live decisions for many states, down the ``runtime`` path with fallthrough on failure.

        The backtest calls this, so it replays the engine the auto-trader runs.
        ``agent_cache`` is passed to ``agent_decisions``.
        """
        start_time = time.perf_counter()

        # 1. Primary: Static Quantized INT8 ONNX Model (Off-thread non-blocking)
        if self._ort_session is not None:
            try:
                return [await asyncio.to_thread(self._run_ort_inference, state) for state in market_states]
            except Exception as e:
                logger.warning(f"INT8 ONNX model inference failed: {e}. Checking secondary runtimes.")

        # 2. Secondary: PyTorch native fine-tuned agent, blended with the rule-based scorer
        if self._agent is not None:
            try:
                model = await asyncio.to_thread(self.agent_decisions, market_states, agent_cache)
                latency_ms = round((time.perf_counter() - start_time) * 1000, 1)
                return [self.blend(dict(agent_res, latency_ms=latency_ms), self.fallback_decision(state, latency_ms))
                        for state, agent_res in zip(market_states, model)]
            except Exception as e:
                logger.warning(f"Native fine-tuned model inference failed: {e}. Trying fallback.")

        # 3. Fallback calibrated System 1 decision engine
        latency_ms = round((time.perf_counter() - start_time) * 1000, 1)
        return [self.fallback_decision(state, latency_ms) for state in market_states]

    _AGENT_QUESTIONS = {
        "position": {
            "type": "choice",
            "instructions": "Determine optimal trading position",
            "criteria": {
                "STRONG_LONG": "Clear upward trend",
                "SCALP_LONG": "Support bounce",
                "NEUTRAL_HOLD": "Choppy range",
                "SCALP_SHORT": "Resistance rejection",
                "STRONG_SHORT": "Clear downward breakdown"
            }
        }
    }

    def agent_summary(self, market_state: Dict[str, Any]) -> str:
        """Text state the native agent is queried with."""
        htf = market_state.get("htf") or {}
        depth = market_state.get("depth") or {}
        adx_val = market_state.get("adx", 20.0)
        obi_val = depth.get("imbalance_pct", 0.0)
        is_sqz = "ACTIVE" if market_state.get("is_squeeze") else "OFF"
        return (
            f"Asset: {market_state.get('symbol', 'BTCUSDT.P')} | "
            f"Micro TF: {market_state.get('interval', '5m')} | "
            f"Price: {market_state.get('price')} | "
            f"1H Macro Trend: {htf.get('trend', 'NEUTRAL')} | "
            f"Market Regime: {market_state.get('market_regime', 'CHOP_RANGE')} | "
            f"ADX: {adx_val:.1f} | "
            f"Order Book OBI: {obi_val:+.1f}% | "
            f"TTM Squeeze: {is_sqz} | "
            f"Trend State: {market_state.get('trend_state')}"
        )

    def agent_decisions(self, market_states: Sequence[Dict[str, Any]],
                        cache: Optional[Dict[str, Dict[str, Any]]] = None,
                        batch_size: int = 32) -> List[Dict[str, Any]]:
        """Native-model decisions (no blend) for one or many states; blocking, call off the event loop.

        ``cache`` maps the SHA-256 of the exact text the model sees to its decision;
        only missing texts are queried, and they are added to it.
        """
        texts = [self.agent_summary(state) for state in market_states]
        keys = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
        cache = {} if cache is None else cache
        missing = [i for i, key in enumerate(keys) if key not in cache]
        if missing:
            todo = [texts[i] for i in missing]
            if len(todo) == 1:
                raw = [self._agent.predict(todo[0], self._AGENT_QUESTIONS)]
            else:
                raw = self._agent.predict_batch(todo, self._AGENT_QUESTIONS, batch_size=batch_size)
            engine_type = f"{self.model_filename}_{self.device}"
            for i, response in zip(missing, raw):
                cache[keys[i]] = self._format_laya_response(response, 0.0, engine_type=engine_type)
        return [dict(cache[key]) for key in keys]

    def blend(self, agent_res: Dict[str, Any], mtf_res: Dict[str, Any]) -> Dict[str, Any]:
        """Blend neural predictions 50/50 with the multi-timeframe rule-based scorer."""
        agent_res = dict(agent_res)
        p_long = 0.50 * agent_res["long_prob"] + 0.50 * mtf_res["long_prob"]
        p_short = 0.50 * agent_res["short_prob"] + 0.50 * mtf_res["short_prob"]
        p_hold = 0.50 * agent_res["hold_prob"] + 0.50 * mtf_res["hold_prob"]

        dir_sum = max(1e-5, p_long + p_short)
        dir_ratio = p_long / dir_sum

        if p_long > p_short and dir_ratio >= 0.525 and p_long >= p_hold * 0.90:
            pos_choice = "STRONG_LONG" if dir_ratio >= 0.55 else "SCALP_LONG"
        elif p_short > p_long and (1.0 - dir_ratio) >= 0.525 and p_short >= p_hold * 0.90:
            pos_choice = "STRONG_SHORT" if (1.0 - dir_ratio) >= 0.55 else "SCALP_SHORT"
        else:
            pos_choice = "NEUTRAL_HOLD"

        conviction = dir_ratio if "LONG" in pos_choice else ((1.0 - dir_ratio) if "SHORT" in pos_choice else max(dir_ratio, 1.0 - dir_ratio))

        agent_res["position"] = pos_choice
        agent_res["long_prob"] = round(p_long, 3)
        agent_res["short_prob"] = round(p_short, 3)
        agent_res["hold_prob"] = round(p_hold, 3)
        agent_res["directional_conviction"] = round(float(conviction), 4)
        agent_res["confidence_prob"] = round(float(conviction), 3)
        return agent_res

    def _format_laya_response(self, raw_data: Dict[str, Any], latency_ms: float, engine_type: str) -> Dict[str, Any]:
        """Format raw Laya server or agent JSON into structured signal data."""
        answers = raw_data.get("answers", {})

        pos_ans = answers.get("position", {})
        pos_choice = pos_ans.get("choice") or pos_ans.get("label", "NEUTRAL_HOLD")
        pos_probs = pos_ans.get("probabilities", {})

        conf_ans = answers.get("confidence", {})
        raw_score = conf_ans.get("score")
        if raw_score is None:
            raw_score = conf_ans.get("expected_score") or conf_ans.get("value") or 3
        try:
            conf_score = round(float(raw_score), 2)
        except Exception:
            conf_score = 3.0

        conf_val = pos_ans.get("answer_confidence") or pos_ans.get("confidence") or pos_probs.get(pos_choice, 0.75)

        squeeze_ans = answers.get("squeeze_risk", {})
        squeeze_prob = squeeze_ans.get("noul")
        if squeeze_prob is None:
            squeeze_prob = squeeze_ans.get("probability", 0.15)

        horizon_ans = answers.get("time_horizon", {})
        horizon = horizon_ans.get("choice") or horizon_ans.get("label", "SCALP")

        # Aggregate probabilities for direction
        p_strong_long = float(pos_probs.get("STRONG_LONG", 0.0))
        p_scalp_long = float(pos_probs.get("SCALP_LONG", 0.0))
        p_neutral = float(pos_probs.get("NEUTRAL_HOLD", 0.0))
        p_scalp_short = float(pos_probs.get("SCALP_SHORT", 0.0))
        p_strong_short = float(pos_probs.get("STRONG_SHORT", 0.0))

        long_agg = p_strong_long + p_scalp_long
        short_agg = p_strong_short + p_scalp_short
        hold_agg = p_neutral

        # If probability is missing, give default distribution based on choice
        if not pos_probs:
            pos_probs = {
                "STRONG_LONG": 0.7 if pos_choice == "STRONG_LONG" else 0.05,
                "SCALP_LONG": 0.6 if pos_choice == "SCALP_LONG" else 0.1,
                "NEUTRAL_HOLD": 0.6 if pos_choice == "NEUTRAL_HOLD" else 0.1,
                "SCALP_SHORT": 0.6 if pos_choice == "SCALP_SHORT" else 0.1,
                "STRONG_SHORT": 0.7 if pos_choice == "STRONG_SHORT" else 0.05,
            }
            long_agg = 0.8 if "LONG" in pos_choice else 0.1
            short_agg = 0.8 if "SHORT" in pos_choice else 0.1
            hold_agg = 0.8 if "HOLD" in pos_choice else 0.1

        # Calculate relative directional conviction between bulls and bears
        dir_sum = max(1e-5, long_agg + short_agg)
        dir_ratio = long_agg / dir_sum
        directional_conviction = dir_ratio if "LONG" in pos_choice else ((1.0 - dir_ratio) if "SHORT" in pos_choice else max(dir_ratio, 1.0 - dir_ratio))

        return {
            "position": pos_choice,
            "probabilities": pos_probs,
            "long_prob": round(long_agg, 3),
            "short_prob": round(short_agg, 3),
            "hold_prob": round(hold_agg, 3),
            "directional_conviction": round(float(directional_conviction), 4),
            "confidence_score": conf_score,
            "confidence_prob": round(float(conf_val), 3),
            "squeeze_risk": round(float(squeeze_prob), 2),
            "time_horizon": horizon,
            "latency_ms": round(float(latency_ms), 1),
            "engine": engine_type
        }

    def fallback_decision(self, market_state: Dict[str, Any], latency_ms: float) -> Dict[str, Any]:
        """
        Institutional-grade calibrated mathematical decision synthesizer.
        Evaluates confluence of:
        1. 1H Macro Trend Confluence (HTF Filter)
        2. Market Regime & Trend Strength (Wilder's ADX 14 & DI)
        3. TTM Squeeze Volatility State
        4. Order Book Imbalance (OBI) & Bid/Ask Ratio
        5. Micro Momentum (RSI, MACD, EMAs, Bollinger %B)
        """
        # Guard every indicator read: `dict.get(k, default)` yields None when the
        # key exists with a null value, and the regime comparisons below are
        # numeric, so a None would raise instead of falling back.
        rsi = market_state.get("rsi")
        rsi = 50.0 if rsi is None else float(rsi)
        macd_hist = market_state.get("macd_hist")
        macd_hist = 0.0 if macd_hist is None else float(macd_hist)
        trend = market_state.get("trend_state", "NEUTRAL")
        bb_pct_b = market_state.get("bb_pct_b")
        bb_pct_b = 0.5 if bb_pct_b is None else float(bb_pct_b)
        vol_spike = market_state.get("vol_spike")
        vol_spike = 1.0 if vol_spike is None else float(vol_spike)
        adx = market_state.get("adx")
        adx = 20.0 if adx is None else float(adx)
        plus_di = market_state.get("plus_di")
        plus_di = 20.0 if plus_di is None else float(plus_di)
        minus_di = market_state.get("minus_di")
        minus_di = 20.0 if minus_di is None else float(minus_di)
        is_squeeze = bool(market_state.get("is_squeeze") or False)

        # HTF Macro Context
        htf = market_state.get("htf") or {}
        htf_trend = htf.get("trend", "NEUTRAL")

        # Order Book & Funding
        depth = market_state.get("depth") or {}
        obi = depth.get("imbalance_pct", 0.0)
        funding = market_state.get("funding") or {}
        funding_rate = funding.get("funding_rate", 0.0)

        # Base scoring
        bull_score = 0.0
        bear_score = 0.0
        hold_score = 0.2

        # 1. 1H Macro Trend Alignment (High Confluence Weight)
        if htf_trend == "STRONG_BULLISH":
            bull_score += 0.45
            bear_score -= 0.30
        elif htf_trend == "BULLISH":
            bull_score += 0.30
            bear_score -= 0.15
        elif htf_trend == "STRONG_BEARISH":
            bear_score += 0.45
            bull_score -= 0.30
        elif htf_trend == "BEARISH":
            bear_score += 0.30
            bull_score -= 0.15
        else:
            hold_score += 0.15

        # 2. Local Trend (5m)
        if trend == "STRONG_BULLISH":
            bull_score += 0.35
        elif trend == "BULLISH":
            bull_score += 0.20
        elif trend == "STRONG_BEARISH":
            bear_score += 0.35
        elif trend == "BEARISH":
            bear_score += 0.20

        # 3. Market Regime & Trend Strength (Wilder's ADX 14)
        if adx < 18.0:
            hold_score += 0.40
            bull_score *= 0.65
            bear_score *= 0.65
        elif adx >= 25.0:
            if plus_di > minus_di + 5:
                bull_score += 0.25
            elif minus_di > plus_di + 5:
                bear_score += 0.25

        # 4. Order Book Imbalance (OBI)
        if obi > 20.0:
            bull_score += min(0.30, (obi / 100.0) * 0.45)
        elif obi < -20.0:
            bear_score += min(0.30, (abs(obi) / 100.0) * 0.45)

        # 5. Funding Rate Overcrowding Shield
        if funding_rate > 0.0003:
            bull_score -= 0.20
        elif funding_rate < -0.0003:
            bear_score -= 0.20

        # 6. Micro Momentum (MACD & RSI)
        if macd_hist > 0:
            bull_score += 0.20
        else:
            bear_score += 0.20

        if rsi < 32:
            bull_score += 0.25
        elif rsi > 68:
            bear_score += 0.25
        elif 52 <= rsi <= 65:
            bull_score += 0.15
        elif 35 <= rsi < 48:
            bear_score += 0.15

        if bb_pct_b < 0.05:
            bull_score += 0.15
        elif bb_pct_b > 0.95:
            bear_score += 0.15

        if vol_spike > 1.3:
            if bull_score > bear_score:
                bull_score += 0.20
            else:
                bear_score += 0.20

        bull_score = max(0.0, bull_score)
        bear_score = max(0.0, bear_score)

        scores = np.array([
            bull_score if bull_score > 0.65 else bull_score * 0.5,
            bull_score * 0.75,
            hold_score + max(0.1, 1.0 - (bull_score + bear_score) * 0.5),
            bear_score * 0.75,
            bear_score if bear_score > 0.65 else bear_score * 0.5
        ])

        exp_s = np.exp(scores * 2.8)
        probs = exp_s / np.sum(exp_s)

        choices = ["STRONG_LONG", "SCALP_LONG", "NEUTRAL_HOLD", "SCALP_SHORT", "STRONG_SHORT"]
        best_idx = int(np.argmax(probs))
        best_choice = choices[best_idx]

        confidence_prob = float(probs[best_idx])
        if confidence_prob > 0.65:
            conf_score = 4
        elif confidence_prob > 0.45:
            conf_score = 3
        elif confidence_prob > 0.3:
            conf_score = 2
        else:
            conf_score = 1

        prob_dict = {
            choices[i]: round(float(probs[i]), 4) for i in range(len(choices))
        }

        long_agg = prob_dict["STRONG_LONG"] + prob_dict["SCALP_LONG"]
        short_agg = prob_dict["STRONG_SHORT"] + prob_dict["SCALP_SHORT"]
        hold_agg = prob_dict["NEUTRAL_HOLD"]

        sq_risk = 0.75 if is_squeeze else min(0.9, vol_spike * 0.25)

        return {
            "position": best_choice,
            "probabilities": prob_dict,
            "long_prob": round(long_agg, 3),
            "short_prob": round(short_agg, 3),
            "hold_prob": round(hold_agg, 3),
            "confidence_score": conf_score,
            "confidence_prob": round(confidence_prob, 3),
            "squeeze_risk": round(sq_risk, 2),
            "time_horizon": "SWING" if "STRONG" in best_choice else ("SCALP" if "SCALP" in best_choice else "WAIT"),
            "latency_ms": latency_ms,
            "engine": "calibrated_system1"
        }

    async def close(self):
        self._ort_session = None
        self._tokenizer = None
        self._agent = None
