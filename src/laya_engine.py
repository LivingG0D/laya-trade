import os
import sys
import json
import time
import logging
import asyncio
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

import httpx
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
        self._is_ready = False
        self._is_server_alive = False

    def is_model_downloaded(self) -> bool:
        return self.model_path.exists() and self.model_path.stat().st_size > 10_000_000

    def download_model(self) -> Path:
        """Download model GGUF from Hugging Face if not already present."""
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
        """Start local laya.exe serve process."""
        if not self.binary_path.exists():
            logger.warning(f"Laya binary not found at {self.binary_path}")
            return False

        if not self.is_model_downloaded():
            logger.warning("Model file not downloaded yet. Run download_model() first.")
            return False

        # First check if a server is already listening
        if await self.check_health():
            logger.info(f"Laya server already active at {self.server_url}")
            self._is_server_alive = True
            self._is_ready = True
            return True

        cmd = [
            str(self.binary_path),
            "serve",
            str(self.model_path),
            "--port", str(self.port),
            "--device", "auto",
            "--cuda-graph"
        ]

        logger.info(f"Launching Laya server: {' '.join(cmd)}")
        try:
            env = self._get_cuda_env()
            self._server_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=str(BASE_DIR)
            )

            # Wait up to 5 seconds for health endpoint
            for _ in range(10):
                await asyncio.sleep(0.5)
                if await self.check_health():
                    logger.info("Laya server started and healthy!")
                    self._is_server_alive = True
                    self._is_ready = True
                    return True

            logger.warning("Laya server did not report healthy within 5 seconds.")
            return False
        except Exception as e:
            logger.error(f"Failed to start Laya server process: {e}")
            return False

    async def check_health(self) -> bool:
        """Check if Laya server is alive and responding."""
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

        # Format state as rich contextual document for Laya System 1 encoder
        summary_text = (
            f"Asset: {market_state.get('symbol', '1000PEPEUSDT')} | Timeframe: {market_state.get('interval', '5m')}\n"
            f"Current Price: {market_state.get('price')} (24h Change: {market_state.get('change_pct')}%)\n"
            f"Trend Regime: {market_state.get('trend_state')}\n"
            f"Key Moving Averages: EMA9={market_state.get('ema9')}, EMA21={market_state.get('ema21')}, EMA50={market_state.get('ema50')}, EMA200={market_state.get('ema200')}\n"
            f"Momentum Oscillators: RSI={market_state.get('rsi')} | MACD={market_state.get('macd')} (Signal: {market_state.get('macd_signal')}, Hist: {market_state.get('macd_hist')})\n"
            f"Bollinger Bands: Upper={market_state.get('bb_upper')}, Lower={market_state.get('bb_lower')}, %B={market_state.get('bb_pct_b')}\n"
            f"Volatility & Volume: ATR={market_state.get('atr')} ({market_state.get('atr_pct')}%) | Volume Spike Factor={market_state.get('vol_spike')}x\n"
            f"Key Structural Levels: Nearest Support={market_state.get('support')}, Nearest Resistance={market_state.get('resistance')}"
        )

        payload = {
            "state": summary_text,
            "questions": questions
        }

        # Try query to live laya server
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

        # Fallback calibrated System 1 decision engine
        latency_ms = round((time.perf_counter() - start_time) * 1000, 1)
        return self._fallback_decision(market_state, latency_ms)

    def _format_laya_response(self, raw_data: Dict[str, Any], latency_ms: float, engine_type: str) -> Dict[str, Any]:
        """Format raw Laya server JSON into structured signal data."""
        answers = raw_data.get("answers", {})

        pos_ans = answers.get("position", {})
        pos_choice = pos_ans.get("choice") or pos_ans.get("label", "NEUTRAL_HOLD")
        pos_probs = pos_ans.get("probabilities", {})

        conf_ans = answers.get("confidence", {})
        conf_score = conf_ans.get("expected_score") or conf_ans.get("value") or 3
        conf_val = pos_ans.get("confidence") or conf_ans.get("confidence", 0.75)

        squeeze_ans = answers.get("squeeze_risk", {})
        squeeze_prob = squeeze_ans.get("probability") or squeeze_ans.get("noul", 0.15)

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
        Calibrated mathematical decision synthesizer.
        Evaluates confluence of RSI, MACD, EMAs, and Bollinger %B.
        """
        rsi = market_state.get("rsi", 50.0)
        macd_hist = market_state.get("macd_hist", 0.0)
        trend = market_state.get("trend_state", "NEUTRAL")
        bb_pct_b = market_state.get("bb_pct_b", 0.5)
        vol_spike = market_state.get("vol_spike", 1.0)

        # Bullish and Bearish confluence scoring
        bull_score = 0.0
        bear_score = 0.0

        if trend == "STRONG_BULLISH":
            bull_score += 0.4
        elif trend == "BULLISH":
            bull_score += 0.25
        elif trend == "STRONG_BEARISH":
            bear_score += 0.4
        elif trend == "BEARISH":
            bear_score += 0.25

        if macd_hist > 0:
            bull_score += 0.25
        else:
            bear_score += 0.25

        if rsi < 35:
            bull_score += 0.2  # Oversold bounce
        elif rsi > 65:
            bear_score += 0.2  # Overbought rejection
        elif 50 <= rsi <= 60:
            bull_score += 0.15 # Bullish momentum
        elif 40 <= rsi < 50:
            bear_score += 0.15 # Bearish momentum

        if bb_pct_b < 0.1:
            bull_score += 0.15 # Rebound from lower band
        elif bb_pct_b > 0.9:
            bear_score += 0.15 # Rebound from upper band

        if vol_spike > 1.5:
            if bull_score > bear_score:
                bull_score += 0.15
            else:
                bear_score += 0.15

        # Normalize probabilities via softmax
        scores = np.array([
            bull_score if bull_score > 0.6 else bull_score * 0.5, # STRONG_LONG
            bull_score * 0.7,                                     # SCALP_LONG
            max(0.2, 1.0 - (bull_score + bear_score) * 0.7),      # NEUTRAL_HOLD
            bear_score * 0.7,                                     # SCALP_SHORT
            bear_score if bear_score > 0.6 else bear_score * 0.5  # STRONG_SHORT
        ])
        import numpy as np
        exp_s = np.exp(scores * 2.5)
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

        return {
            "position": best_choice,
            "probabilities": prob_dict,
            "long_prob": round(long_agg, 3),
            "short_prob": round(short_agg, 3),
            "hold_prob": round(hold_agg, 3),
            "confidence_score": conf_score,
            "confidence_prob": round(confidence_prob, 3),
            "squeeze_risk": round(min(0.9, vol_spike * 0.25), 2),
            "time_horizon": "SWING" if "STRONG" in best_choice else ("SCALP" if "SCALP" in best_choice else "WAIT"),
            "latency_ms": latency_ms,
            "engine": "calibrated_system1"
        }

    async def close(self):
        if self._server_process:
            self._server_process.terminate()
            self._server_process = None
        await self._http_client.aclose()
