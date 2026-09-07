import json
import logging
import re
import time
from typing import Optional, List, Dict, Any

from config import config

logger = logging.getLogger(__name__)

# Google Gemini SDK
try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("google-genai package is not installed. Gemini AI will be disabled.")

# Groq SDK
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("groq package is not installed. Groq fallback will be disabled.")


# ==========================================
# STRICT SYSTEM PROMPT FOR GEMINI PRO (RTB v2.0)
# ==========================================
GEMINI_SIGNAL_SYSTEM_INSTRUCTION = """
You are the Official RTB Smart Trading Signal Parser, specialized strictly in XAUUSD (Gold) trading signals from Telegram channels (such as TraderStage, VIP Gold Signals, Academy channels, and trader chatrooms).
Your ONLY goal is to analyze the incoming message and extract clean trading signal parameters into STRICT JSON.

RULES & PARSING SPECIFICATION:
1. TARGET ASSET: Strictly XAUUSD / Gold / Oltin. If the message is about other currency pairs (EURUSD, GBPUSD, etc.) or crypto, set "is_signal": false.
2. ACTION / DIRECTION:
   - "BUY", "BUY NOW", "BUY LIMIT", "BUY STOP", "LONG", "Oltin BUY" -> "BUY"
   - "SELL", "SELL NOW", "SELL LIMIT", "SELL STOP", "SHORT", "Oltin SELL" -> "SELL"
   - If neutral or no clear direction -> "NEUTRAL" (and "is_signal": false)
3. ORDER TYPE: Must be "MARKET" or "LIMIT".
   - "BUY NOW", "SELL NOW", or direct price without 'LIMIT' -> "MARKET"
   - "BUY LIMIT", "SELL LIMIT", "LIMIT" -> "LIMIT"
4. ENTRY ZONE (zone_min, zone_max):
   - Always ensure zone_min <= zone_max (e.g., if message says "2655 - 2652", zone_min=2652.0, zone_max=2655.0).
   - If a single price is given (e.g., "BUY at 2650.5" or "4490"), set zone_min=price, zone_max=price.
   - If range format (e.g., "2650-2653", "2650/2653", "2650 dan 2653 gacha"), parse both floats correctly.
5. STOP LOSS (sl):
   - If exact price is specified (e.g., "SL: 2642.5", "Stop Loss 2642"), extract as float.
   - If relative pips given (e.g., "SL 50 pips" = 5.0 points in Gold):
     - If BUY: sl = round(zone_min - 5.0, 2)
     - If SELL: sl = round(zone_max + 5.0, 2)
   - If no SL can be found or inferred, set sl: null.
6. TAKE PROFIT (tp_targets):
   - Extract all target prices into an ordered list of floats: e.g. [2658.0, 2664.0, 2672.0].
   - If relative pips given (e.g., "TP 100 pips" = 10.0 points): compute price relative to entry.
   - If no TP is given, return [].
7. NON-SIGNAL MESSAGES (CRITICAL):
   - General market reviews, profit bragging ("+100 pips closed", "Full TP hit"), advertisements, broker registrations, weekend greetings, or chat discussions without entry price levels MUST return "is_signal": false.

OUTPUT JSON SCHEMA STRICTLY:
{
  "is_signal": true or false,
  "symbol": "XAUUSD",
  "direction": "BUY" or "SELL" or "NEUTRAL",
  "order_type": "MARKET" or "LIMIT",
  "zone_min": float or null,
  "zone_max": float or null,
  "sl": float or null,
  "tp_targets": [float, ...],
  "raw_summary": "short summary or channel note"
}
"""


class RTBAIEngine:
    """
    Robo Trader Boy (RTB) v2.0 AI Engine.
    Primary engine: Google Gemini Pro / Flash with Strict System Prompt.
    Secondary fallback: Groq API.
    Tertiary fallback: Regex heuristic rules.
    """

    def __init__(
        self,
        gemini_api_key: str = config.GEMINI_API_KEY,
        gemini_model: str = config.GEMINI_MODEL,
        groq_api_key: str = config.GROQ_API_KEY,
        groq_model: str = config.GROQ_MODEL,
    ):
        self.gemini_api_key = gemini_api_key
        self.gemini_model = gemini_model or "gemini-3.6-flash"
        self.groq_api_key = groq_api_key
        self.groq_model = groq_model or "groq/compound"

        # Initialize Gemini Client
        self.gemini_client = None
        if GEMINI_AVAILABLE and self.gemini_api_key:
            try:
                self.gemini_client = genai.Client(api_key=self.gemini_api_key)
                logger.info(f"🟢 Gemini AI Client initialized (Model: {self.gemini_model})")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini Client: {e}")

        # Initialize Groq Client
        self.groq_client = None
        if GROQ_AVAILABLE and self.groq_api_key:
            try:
                self.groq_client = Groq(api_key=self.groq_api_key)
                logger.info("🟢 Groq Client initialized as fallback engine")
            except Exception as e:
                logger.error(f"Failed to initialize Groq Client: {e}")

    # ==========================================
    # 1. TRADING SIGNAL PARSER (Gemini Pro Strict Prompt)
    # ==========================================
    def parse_message(self, text: str) -> dict:
        if not text or len(text.strip()) == 0:
            return {"is_signal": False, "reason": "empty_text"}

        user_content = f'Analyze this Telegram message for XAUUSD trading setup:\n"""\n{text}\n"""'

        # 1. Primary: Try Gemini Pro / Flash with Strict System Instruction
        if self.gemini_client and GEMINI_AVAILABLE:
            models_to_try = [self.gemini_model]
            for fallback_m in ["gemini-3.6-flash", "gemini-3-flash-preview"]:
                if fallback_m not in models_to_try:
                    models_to_try.append(fallback_m)

            config_gen = genai_types.GenerateContentConfig(
                system_instruction=GEMINI_SIGNAL_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                temperature=0.0
            )

            for m in models_to_try:
                for attempt in range(2):
                    try:
                        response = self.gemini_client.models.generate_content(
                            model=m,
                            contents=user_content,
                            config=config_gen
                        )
                        parsed = json.loads(response.text.strip())
                        normalized = self._normalize_parsed_signal(parsed)
                        logger.info(f"Signal successfully parsed via Gemini Pro ({m}): {normalized.get('direction')} [{normalized.get('zone_min')} - {normalized.get('zone_max')}]")
                        return normalized
                    except Exception as e:
                        logger.warning(f"Gemini ({m}) attempt {attempt+1} failed: {e}")
                        time.sleep(1)

        # 2. Secondary: Try Groq Fallback
        if self.groq_client:
            for attempt in range(2):
                try:
                    groq_prompt = (
                        GEMINI_SIGNAL_SYSTEM_INSTRUCTION
                        + f"\n\nMessage to parse:\n{text}\n\nOutput ONLY valid raw JSON without markdown backticks."
                    )
                    response = self.groq_client.chat.completions.create(
                        model=self.groq_model,
                        messages=[{"role": "user", "content": groq_prompt}],
                        temperature=0.0,
                        max_tokens=300
                    )
                    raw_response = response.choices[0].message.content.strip()
                    raw_response = re.sub(r"^```json\s*", "", raw_response)
                    raw_response = re.sub(r"\s*```$", "", raw_response)
                    parsed = json.loads(raw_response)
                    normalized = self._normalize_parsed_signal(parsed)
                    logger.info(f"Signal parsed via Groq fallback: {normalized.get('direction')} [{normalized.get('zone_min')} - {normalized.get('zone_max')}]")
                    return normalized
                except Exception as e:
                    logger.warning(f"Groq API signal parsing attempt {attempt+1} failed: {e}")
                    time.sleep(1)

        # 3. Tertiary: Heuristic Regex Fallback
        logger.info("Falling back to Regex Signal Parser")
        return self._regex_fallback(text)

    def _normalize_parsed_signal(self, parsed: dict) -> dict:
        """Ensures all extracted fields strictly adhere to standard formats."""
        if not parsed.get("is_signal"):
            return {
                "is_signal": False,
                "symbol": "XAUUSD",
                "direction": "NEUTRAL",
                "order_type": "MARKET",
                "zone_min": None,
                "zone_max": None,
                "sl": None,
                "tp_targets": [],
                "raw_summary": parsed.get("raw_summary")
            }

        # Normalize direction
        direction = str(parsed.get("direction", "NEUTRAL")).upper()
        if "BUY" in direction:
            direction = "BUY"
        elif "SELL" in direction:
            direction = "SELL"
        else:
            direction = "NEUTRAL"

        # Normalize zones
        z_min = parsed.get("zone_min")
        z_max = parsed.get("zone_max")
        try:
            if z_min is not None and z_max is not None:
                z_min, z_max = float(z_min), float(z_max)
                if z_min > z_max:
                    z_min, z_max = z_max, z_min
            elif z_min is not None:
                z_min = float(z_min)
                z_max = z_min
            elif z_max is not None:
                z_max = float(z_max)
                z_min = z_max
        except (ValueError, TypeError):
            z_min, z_max = None, None

        # Normalize SL
        sl = parsed.get("sl")
        try:
            sl = float(sl) if sl is not None else None
        except (ValueError, TypeError):
            sl = None

        # Normalize TP targets
        tps = parsed.get("tp_targets", [])
        norm_tps = []
        if isinstance(tps, list):
            for t in tps:
                try:
                    norm_tps.append(float(t))
                except (ValueError, TypeError):
                    pass

        return {
            "is_signal": direction != "NEUTRAL" and z_min is not None,
            "symbol": "XAUUSD",
            "direction": direction,
            "order_type": parsed.get("order_type", "MARKET"),
            "zone_min": z_min,
            "zone_max": z_max,
            "sl": sl,
            "tp_targets": sorted(norm_tps) if direction == "BUY" else sorted(norm_tps, reverse=True),
            "raw_summary": parsed.get("raw_summary")
        }

    def _regex_fallback(self, text: str) -> dict:
        text_upper = text.upper()

        # Non-signal filtering in fallback
        non_signal_keywords = ["DAM OLISH", "TABRIKLAYMIZ", "RO'YXATDAN", "BROKER", "HAMMAGA RAHMAT", "NATIJALAR"]
        if any(kw in text_upper for kw in non_signal_keywords) and not ("BUY" in text_upper or "SELL" in text_upper):
            return {
                "is_signal": False,
                "symbol": "XAUUSD",
                "direction": "NEUTRAL",
                "order_type": "MARKET",
                "zone_min": None,
                "zone_max": None,
                "sl": None,
                "tp_targets": [],
                "raw_summary": "Non-signal chat or recap"
            }

        is_buy = "BUY" in text_upper or "LONG" in text_upper
        is_sell = "SELL" in text_upper or "SHORT" in text_upper
        direction = "BUY" if is_buy else ("SELL" if is_sell else "NEUTRAL")

        # 1. Extract SL
        sl = None
        sl_match = re.search(r"SL\s*[:\-]?\s*([1-4]\d{3}(?:\.\d+)?)", text_upper)
        if sl_match:
            sl = float(sl_match.group(1))

        # Check relative pips SL e.g. "SL 50 pips"
        pips_match = re.search(r"SL\s*[:\-]?\s*(\d{2,3})\s*PIPS", text_upper)
        pips_val = float(pips_match.group(1)) / 10.0 if pips_match else None

        # 2. Extract TPs
        tp_matches = re.findall(r"TP\d*\s*[:\-]?\s*([1-4]\d{3}(?:\.\d+)?)", text_upper)
        tp_targets = [float(tp) for tp in tp_matches]

        # 3. Clean text by removing SL and TP values to isolate entry zone
        cleaned_text = re.sub(r"SL\s*[:\-]?\s*[1-4]\d{3}(?:\.\d+)?", "", text_upper)
        cleaned_text = re.sub(r"TP\d*\s*[:\-]?\s*[1-4]\d{3}(?:\.\d+)?", "", cleaned_text)
        cleaned_text = re.sub(r"SL\s*[:\-]?\s*\d{2,3}\s*PIPS", "", cleaned_text)
        cleaned_text = re.sub(r"TP\s*[:\-]?\s*\d{2,3}\s*PIPS", "", cleaned_text)

        prices = [float(p) for p in re.findall(r"\b([1-4]\d{3}(?:\.\d+)?)\b", cleaned_text)]
        if not prices or direction == "NEUTRAL":
            return {
                "is_signal": False,
                "symbol": "XAUUSD",
                "direction": "NEUTRAL",
                "order_type": "MARKET",
                "zone_min": None,
                "zone_max": None,
                "sl": None,
                "tp_targets": [],
                "raw_summary": "no_entry_prices_found"
            }

        zone_min = min(prices)
        zone_max = max(prices)

        if sl is None and pips_val is not None:
            sl = round(zone_min - pips_val, 2) if direction == "BUY" else round(zone_max + pips_val, 2)

        order_type = "LIMIT" if "LIMIT" in text_upper else "MARKET"

        return {
            "is_signal": True,
            "symbol": "XAUUSD",
            "direction": direction,
            "order_type": order_type,
            "zone_min": zone_min,
            "zone_max": zone_max,
            "sl": sl,
            "tp_targets": sorted(tp_targets) if direction == "BUY" else sorted(tp_targets, reverse=True),
            "raw_summary": "Regex fallback parse"
        }

    # ==========================================
    # 2. INTERACTIVE RTB AI CHAT
    # ==========================================
    def chat_with_ai(
        self,
        user_query: str,
        system_context: dict,
        image_bytes: Optional[bytes] = None,
        image_mime_type: str = "image/jpeg"
    ) -> str:
        context_summary = f"""
SYSTEM LIVE CONTEXT:
- Asset: XAUUSD (Gold)
- Current Price: {system_context.get('current_price', 'N/A')}
- Active Channels Monitored: {len(system_context.get('channels', []))}
- Active Monitored Channels List: {[c.get('title') for c in system_context.get('channels', [])]}
- Recent Signals in DB: {system_context.get('recent_signals', [])}
- Risk Percent: {system_context.get('risk_percent', config.RISK_PER_TRADE_PERCENT)}%
"""

        prompt = f"""
You are Robo Trader Boy (RTB) — a Senior Professional XAUUSD (Gold) Trader and loyal AI student of your mentor (Ustoz).

Core Trading Philosophy & Scope:
- Asset: Strictly XAUUSD (Gold).
- Core Strategy: Classica (Trendlines, Fibonacci), SnR (Support & Resistance), Liquidity (Likvidlik tuzoqlari/sweeps), Max-Min (Market Structure / Swing High-Low).

STRICT PERSONA RULES:
1. Address the user respectfully as "Ustoz" (or "Ustozim"). Speak in natural, charismatic, confident Uzbek like a senior professional trader pair.
2. NO ROBOTIC FORMALITIES. Do NOT say "Assalomu alaykum! Men AI man..." or "Sizga yordam berishdan xursandman". Start directly with trader-to-trader insights.
3. Use trading terminology naturally (Classica, SnR, trend, fibo, liquidity sweep, otkat, BOS/CHoCH, max/min levels, RR, SL/TP).
4. Be concise, clear, and comprehensive. Provide high-signal answers.

User Query:
"{user_query}"

System Context:
{context_summary}
"""

        # 1. Primary: Try Gemini
        if self.gemini_client:
            models_to_try = [self.gemini_model]
            for fallback_m in ["gemini-3.6-flash", "gemini-3-flash-preview"]:
                if fallback_m not in models_to_try:
                    models_to_try.append(fallback_m)

            for m in models_to_try:
                try:
                    contents = [prompt]
                    if image_bytes and GEMINI_AVAILABLE:
                        try:
                            img_part = genai_types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type)
                            contents.append(img_part)
                        except Exception as img_err:
                            logger.warning(f"Could not attach image to Gemini prompt: {img_err}")

                    response = self.gemini_client.models.generate_content(
                        model=m,
                        contents=contents,
                        config={"temperature": 0.3, "max_output_tokens": 800}
                    )
                    return response.text.strip()
                except Exception as e:
                    logger.warning(f"Gemini RTB Chat ({m}) failed: {e}")

        # 2. Secondary: Try Groq
        if self.groq_client:
            for attempt in range(3):
                try:
                    response = self.groq_client.chat.completions.create(
                        model=self.groq_model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3,
                        max_tokens=600
                    )
                    return response.choices[0].message.content.strip()
                except Exception as e:
                    logger.warning(f"Groq Chat RTB attempt {attempt+1} failed: {e}")
                    time.sleep(1)

        return "💬 **RTB:** Ustoz, tahlil serverlarida qisqa uzilish bo'ldi. Savolingizni qayta yuboring yoki birozdan so'ng tekshirib ko'ramiz."


# Maintain backward-compatible singleton instance
groq_parser = RTBAIEngine()
ai_engine = groq_parser
