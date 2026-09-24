import os
import sys
import json
import time
import logging
import asyncio
import warnings
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

import httpx
import torch
import laya
import numpy as np
from huggingface_hub import hf_hub_download

from .config import (
    BASE_DIR,
    MODELS_DIR,
    BIN_DIR,
    HF_REPO_ID,
    DEFAULT_MODEL_FILE,
    LAYA_SERVER_HOST,
    LAYA_SERVER_PORT,
)

logger = logging.getLogger("laya_engine")

class LayaEngine:
    def __init__(
        self,
        repo_id: str = HF_REPO_ID,
        model_filename: str = DEFAULT_MODEL_FILE,
        host: str = LAYA_SERVER_HOST,
        port: int = LAYA_SERVER_PORT,
        device: Optional[str] = None,
    ):
        self.repo_id = repo_id
        self.model_filename = model_filename
        self.host = host
        self.port = port
        self.model_path = MODELS_DIR / model_filename
        self.binary_path = BIN_DIR / ("laya.exe" if sys.platform == "win32" else "laya")
        self.server_url = f"http://{self.host}:{self.port}"
        self._http_client = httpx.AsyncClient(timeout=10.0)
        self._server_process: Optional[subprocess.Popen] = None
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._agent: Optional[Any] = None
        self._is_ready = False
        self._is_server_alive = False
        self._load_lock = asyncio.Lock()

    @property
    def is_ready(self) -> bool:
        return self._is_ready and (self._agent is not None or self._is_server_alive)

    def is_model_downloaded(self) -> bool:
        if self.model_path.is_dir():
            st_file = self.model_path / "model.safetensors"
            return st_file.exists() and st_file.stat().st_size > 10_000_000
        return self.model_path.exists() and self.model_path.stat().st_size > 10_000_000

    def download_model(self) -> Path:
        """Download model from Hugging Face if not already present."""
        if self.is_model_downloaded():
            logger.info(f"Model already present at {self.model_path}")
            return self.model_path

        logger.info(f"Downloading {self.model_filename} from {self.repo_id}...")
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

    def _get_cuda_env(self) -> Dict[str, str]:
        """Construct environment containing CUDA runtime DLL paths if present in site-packages."""
        env = os.environ.copy()
        
        # Check standard python site-packages for nvidia cuda/cublas dlls
        site_packages = [
            Path(sys.prefix) / "Lib" / "site-packages",
            Path(sys.base_prefix) / "Lib" / "site-packages",
        ]
        
        extra_paths = [str(BIN_DIR)]
        for sp in site_packages:
            if sp.exists():
                for sub in ["nvidia/cuda_runtime/bin", "nvidia/cublas/bin", "nvidia/cublas/lib", "torch/lib"]:
                    candidate = sp / sub
                    if candidate.exists():
                        extra_paths.append(str(candidate))

        current_path = env.get("PATH", "")
        env["PATH"] = ";".join(extra_paths) + ";" + current_path
        return env

    async def start_server(self) -> bool:
        """Load native fine-tuned Laya crypto model or launch local laya.exe serve process."""
        async with self._load_lock:
            # 1. Native fine-tuned Laya crypto model (safetensors checkpoint)
            if self.model_path.is_dir() and (self.model_path / "model.safetensors").exists():
                try:
                    logger.info(f"Loading fine-tuned Laya crypto model from {self.model_path} on {self.device}...")
                    warnings.filterwarnings("ignore", category=RuntimeWarning, module="laya.*")
                    self._agent = await asyncio.to_thread(
                        laya.load,
                        str(self.model_path),
                        device=self.device
                    )
                    self._is_ready = True
                    self._is_server_alive = True
                    logger.info(f"Fine-tuned Laya crypto model loaded successfully on {self.device}!")
                    asyncio.create_task(self._warmup())
                    return True
                except Exception as e:
                    logger.error(f"Failed to load fine-tuned model with laya.load: {e}", exc_info=True)

            # 2. Check if external Laya server is already responding
            if await self.check_health():
                logger.info(f"Laya server already active at {self.server_url}")
                self._is_server_alive = True
                self._is_ready = True
                return True

            # 3. GGUF binary serve fallback if model is a .gguf file
            if self.binary_path.exists() and self.model_path.is_file() and self.model_path.suffix == ".gguf":
                cmd = [
                    str(self.binary_path),
                    "serve",
                    str(self.model_path),
                    "--port", str(self.port),
                    "--device", "auto",
                    "--cuda-graph"
                ]
                logger.info(f"Launching Laya GGUF server: {' '.join(cmd)}")
                try:
                    env = self._get_cuda_env()
                    self._server_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=env,
                        cwd=str(BASE_DIR)
                    )
                    for _ in range(10):
                        await asyncio.sleep(0.5)
                        if await self.check_health():
                            logger.info("Laya GGUF server started and healthy!")
                            self._is_server_alive = True
                            self._is_ready = True
                            return True
                except Exception as e:
                    logger.error(f"Failed to start Laya GGUF server process: {e}")

            logger.warning("Could not initialize native or GGUF Laya server. Internal calibrated engine will be used.")
            return False

    async def _warmup(self):
        """Warm up fine-tuned model asynchronously."""
        if self._agent is None:
            return
        try:
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

    async def check_health(self) -> bool:
        """Check if Laya server is alive and responding or agent is loaded."""
        if self._agent is not None:
            return True
        try:
            resp = await self._http_client.get(f"{self.server_url}/health", timeout=1.5)
            if resp.status_code == 200:
                self._is_server_alive = True
                return True
        except Exception:
            pass
        self._is_server_alive = False
        return False

    async def decide(self, market_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute System-1 decision evaluation on the given market state.
        Returns parsed decision choices, probabilities, and scores.
        """
        start_time = time.perf_counter()

        # Build typed questions for crypto trading
        questions = {
            "position": {
                "type": "choice",
                "instructions": (
                    "Based on technical signals, trend, momentum, volatility, and orderbook flow, "
                    "what is the optimal trading position?"
                ),
                "criteria": {
                    "STRONG_LONG": "Clear upward trend, price above key EMAs, healthy RSI, expanding bullish volume",
                    "SCALP_LONG": "Support bounce, oversold reversal, slight upward momentum",
                    "NEUTRAL_HOLD": "Choppy range, conflicting indicators, high risk of false breakout",
                    "SCALP_SHORT": "Resistance rejection, overbought pullback, slight downward pressure",
                    "STRONG_SHORT": "Clear downward breakdown, price below key EMAs, heavy bearish volume"
                }
            },
            "confidence": {
                "type": "score",
                "instructions": "How strong and reliable is this trading opportunity?",
                "criteria": [
                    "Very Low (Uncertain/Chop)",
                    "Moderate (Standard setup)",
                    "High (Clean confluence)",
                    "Extreme (High conviction breakout/breakdown)"
                ]
            },
            "squeeze_risk": {
                "type": "noul",
                "instructions": "Is there a high risk of violent short/long squeeze or sudden whipsaw?"
            },
            "time_horizon": {
                "type": "choice",
                "instructions": "What is the recommended trade duration?",
                "criteria": {
                    "SCALP": "Fast 1m to 5m move to nearest support/resistance",
                    "SWING": "Multi-candle trend move across 15m to 1h",
                    "WAIT": "No trade, wait for cleaner market structure"
                }
            }
        }

        # Extract enriched institutional context
        htf = market_state.get("htf") or {}
        depth = market_state.get("depth") or {}
        funding = market_state.get("funding") or {}

        htf_str = f"1H Trend: {htf.get('trend', 'NEUTRAL')} (1H EMA50: {htf.get('ema50', 'N/A')}, 1H EMA200: {htf.get('ema200', 'N/A')}, 1H RSI: {htf.get('rsi', 'N/A')})"
        regime_str = f"Regime: {market_state.get('market_regime', 'CHOP_RANGE')} | Wilder ADX(14): {market_state.get('adx', 20.0)} (+DI: {market_state.get('plus_di', 20.0)}, -DI: {market_state.get('minus_di', 20.0)})"
        squeeze_str = f"TTM Squeeze: {market_state.get('squeeze_status', 'NORMAL')} (Compressed: {market_state.get('is_squeeze', False)})"
        depth_str = f"Order Book Imbalance (OBI): {depth.get('imbalance_pct', 0.0)}% | Bid/Ask Ratio: {depth.get('bid_ask_ratio', 1.0)} (Bids: {depth.get('bid_volume', 0.0)}, Asks: {depth.get('ask_volume', 0.0)})"
        funding_str = f"Perp Funding Rate: {funding.get('funding_rate_pct', 0.0)}% (Rate: {funding.get('funding_rate', 0.0)})"

        # Format state as rich contextual document for Laya System 1 encoder
        summary_text = (
            f"Asset: {market_state.get('symbol', 'BTCUSDT.P')} | Micro TF: {market_state.get('interval', '5m')}\n"
            f"Current Price: {market_state.get('price')} (24h Change: {market_state.get('change_pct')}%\n"
            f"Macro Confluence: {htf_str}\n"
            f"Market Dynamics: {regime_str} | {squeeze_str}\n"
            f"Order Flow & Liquidity: {depth_str} | {funding_str}\n"
            f"Local Trend State: {market_state.get('trend_state')}\n"
            f"Moving Averages (Micro): EMA9={market_state.get('ema9')}, EMA21={market_state.get('ema21')}, EMA50={market_state.get('ema50')}, EMA200={market_state.get('ema200')}\n"
            f"Momentum Oscillators: RSI={market_state.get('rsi')} | MACD={market_state.get('macd')} (Signal: {market_state.get('macd_signal')}, Hist: {market_state.get('macd_hist')})\n"
            f"Bollinger Bands: Upper={market_state.get('bb_upper')}, Lower={market_state.get('bb_lower')}, %B={market_state.get('bb_pct_b')}\n"
            f"Volatility & Volume: ATR={market_state.get('atr')} ({market_state.get('atr_pct')}%) | RVOL={market_state.get('rvol', 1.0)}x | Vol Surge={market_state.get('vol_spike')}x\n"
            f"Key Structural Levels: Nearest Support={market_state.get('support')}, Nearest Resistance={market_state.get('resistance')}"
        )

        payload = {
            "state": summary_text,
            "questions": questions
        }

        # 1. Native in-process fine-tuned Laya crypto agent (CUDA/PyTorch)
        if self._agent is not None:
            try:
                res = await asyncio.to_thread(self._agent.predict, summary_text, questions)
                latency_ms = round((time.perf_counter() - start_time) * 1000, 1)
                return self._format_laya_response(
                    res,
                    latency_ms,
                    engine_type=f"fine_tuned_laya_crypto_{self.device}"
                )
            except Exception as e:
                logger.warning(f"Native fine-tuned model inference failed: {e}. Trying fallback.")

        # 2. Remote / local GGUF server
        if self._is_server_alive or await self.check_health():
            try:
                resp = await self._http_client.post(
                    f"{self.server_url}/v1/decide",
                    json=payload,
                    timeout=5.0
                )
                if resp.status_code == 200:
                    data = resp.json()
                    latency_ms = data.get("usage", {}).get("latency_ms") or round((time.perf_counter() - start_time) * 1000, 1)
                    return self._format_laya_response(data, latency_ms, engine_type="cuda_laya_server")
            except Exception as e:
                logger.warning(f"Laya server request failed: {e}. Falling back to internal decision engine.")

        # 3. Fallback calibrated System 1 decision engine
        latency_ms = round((time.perf_counter() - start_time) * 1000, 1)
        return self._fallback_decision(market_state, latency_ms)

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

        return {
            "position": pos_choice,
            "probabilities": pos_probs,
            "long_prob": round(long_agg, 3),
            "short_prob": round(short_agg, 3),
            "hold_prob": round(hold_agg, 3),
            "confidence_score": conf_score,
            "confidence_prob": round(float(conf_val), 3),
            "squeeze_risk": round(float(squeeze_prob), 2),
            "time_horizon": horizon,
            "latency_ms": round(float(latency_ms), 1),
            "engine": engine_type
        }

    def _fallback_decision(self, market_state: Dict[str, Any], latency_ms: float) -> Dict[str, Any]:
        """
        Institutional-grade calibrated mathematical decision synthesizer.
        Evaluates confluence of:
        1. 1H Macro Trend Confluence (HTF Filter)
        2. Market Regime & Trend Strength (Wilder's ADX 14 & DI)
        3. TTM Squeeze Volatility State
        4. Order Book Imbalance (OBI) & Bid/Ask Ratio
        5. Micro Momentum (RSI, MACD, EMAs, Bollinger %B)
        """
        rsi = market_state.get("rsi", 50.0)
        macd_hist = market_state.get("macd_hist", 0.0)
        trend = market_state.get("trend_state", "NEUTRAL")
        bb_pct_b = market_state.get("bb_pct_b", 0.5)
        vol_spike = market_state.get("vol_spike", 1.0)
        adx = market_state.get("adx", 20.0)
        plus_di = market_state.get("plus_di", 20.0)
        minus_di = market_state.get("minus_di", 20.0)
        is_squeeze = market_state.get("is_squeeze", False)
        
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
            # Low ADX indicates chop range / whipsaw market
            hold_score += 0.40
            bull_score *= 0.65
            bear_score *= 0.65
        elif adx >= 25.0:
            # Strong trending market
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
        if funding_rate > 0.0003: # > +0.03% (crowded longs)
            bull_score -= 0.20
        elif funding_rate < -0.0003: # < -0.03% (crowded shorts)
            bear_score -= 0.20

        # 6. Micro Momentum (MACD & RSI)
        if macd_hist > 0:
            bull_score += 0.20
        else:
            bear_score += 0.20

        if rsi < 32:
            bull_score += 0.25  # Oversold bounce
        elif rsi > 68:
            bear_score += 0.25  # Overbought rejection
        elif 52 <= rsi <= 65:
            bull_score += 0.15  # Healthy bullish continuation
        elif 35 <= rsi < 48:
            bear_score += 0.15  # Bearish continuation

        if bb_pct_b < 0.05:
            bull_score += 0.15
        elif bb_pct_b > 0.95:
            bear_score += 0.15

        if vol_spike > 1.3:
            if bull_score > bear_score:
                bull_score += 0.20
            else:
                bear_score += 0.20

        # Floor at 0.0
        bull_score = max(0.0, bull_score)
        bear_score = max(0.0, bear_score)

        # Build candidate vector
        scores = np.array([
            bull_score if bull_score > 0.65 else bull_score * 0.5, # STRONG_LONG
            bull_score * 0.75,                                     # SCALP_LONG
            hold_score + max(0.1, 1.0 - (bull_score + bear_score) * 0.5), # NEUTRAL_HOLD
            bear_score * 0.75,                                     # SCALP_SHORT
            bear_score if bear_score > 0.65 else bear_score * 0.5  # STRONG_SHORT
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

        # Long vs Short vs Hold aggregated
        long_agg = prob_dict["STRONG_LONG"] + prob_dict["SCALP_LONG"]
        short_agg = prob_dict["STRONG_SHORT"] + prob_dict["SCALP_SHORT"]
        hold_agg = prob_dict["NEUTRAL_HOLD"]

        # Squeeze risk calculation
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
        if self._agent is not None:
            self._agent = None
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
        if self._server_process:
            self._server_process.terminate()
            self._server_process = None
        await self._http_client.aclose()
