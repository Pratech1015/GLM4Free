#!/usr/bin/env python3
"""
Pure-HTTP Aliyun NoCaptcha (TRACELESS) client for Z.ai.

Replicates browser captcha flow without a browser:
  Log1/Log2/Log3 (device) -> InitCaptchaV3 -> VerifyCaptchaV3
  -> securityToken -> captcha_verify_param
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import aiohttp
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# --- Reverse-engineered constants (AliyunCaptcha.js) ---

ACCESS_SEC = b"FqJB6iRNVYdEGpwb"
AES_IV = base64.b64encode(bytes.fromhex("d35db7e39ebbf3d001083105"))  # b"0123456789ABCDEF"

# AES session keys (hex, 16 bytes)
KEY_REQ = "45f8ac1e1de14397"
KEY_RES = "87f879f135f27da7"
KEY_FLAG = "c175a358550d02e2"
KEY_UPLOAD = "a549a55c60a39aa0"
KEY_PREID = "75ae5c150d235802"

# Access key pairs (decrypted with ACCESS_SEC)
DEVICE_KEY_ID = "DuaneAprqkYsF3nt1yjK29Bf"
DEVICE_KEY_SECRET = "DuanemHmyeE6LXCC46sJEDUw5DTlSZ"
CAPTCHA_KEY_ID = "111jdk439dJJIjd023823201"
CAPTCHA_KEY_SECRET = "222aiJodos2938JDdosko2djd82sf0"

# Site config for chat.z.ai
SCENE_ID = "didk33e0"
PREFIX = "no8xfe"
REGION = "sgp"
PLATFORM = "W.10001.c"
APP_NAME = "saf-captcha"
APP_NAME_DEVICE = "saf-captcha"
APP_KEY = "3795d28242a11619bc25f786f84e53d4"  # scene/region appKey (also device-id prefix)
APP_VERSION = "W20220202"
DEVICE_TYPE_WEB = "W"
API_VERSION_CAPTCHA = "2023-03-05"
API_VERSION_DEVICE = "2020-10-15"

CLOUDAUTH_URL = "https://cloudauth-device-dualstack.ap-southeast-1.aliyuncs.com/"
CAPTCHA_URL = f"https://{PREFIX}.captcha-open-southeast.aliyuncs.com/"
CAPTCHA_VERIFY_URL = f"https://{PREFIX}-verify.captcha-open-southeast.aliyuncs.com/"
UPLOAD_URL = f"https://upload.captcha-open-southeast.aliyuncs.com/"

CAPTCHA_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://chat.z.ai",
    "Referer": "https://chat.z.ai/",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:151.0) "
        "Gecko/20100101 Firefox/151.0"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _aes_encrypt_b64(plaintext: str, key_hex: str) -> str:
    key = bytes.fromhex(key_hex) if len(key_hex) == 32 else key_hex.encode("utf-8")
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(AES_IV), backend=default_backend()).encryptor()
    return base64.b64encode(enc.update(padded) + enc.finalize()).decode("ascii")


def _aes_decrypt_b64(b64: str, key_hex: str) -> bytes:
    key = bytes.fromhex(key_hex) if len(key_hex) == 32 else key_hex.encode("utf-8")
    ct = base64.b64decode(b64)
    dec = Cipher(algorithms.AES(key), modes.CBC(AES_IV), backend=default_backend()).decryptor()
    pt = dec.update(ct) + dec.finalize()
    pad = pt[-1]
    if 1 <= pad <= 16 and pt[-pad:] == bytes([pad]) * pad:
        pt = pt[:-pad]
    return pt


def _aliyun_percent_encode(s: str) -> str:
    return quote(str(s), safe="~").replace("+", "%20").replace("*", "%2A").replace("%7E", "~")


def _sign_rpc(params: dict[str, str], secret: str) -> str:
    items = sorted((k, v) for k, v in params.items() if k != "Signature")
    canonical = "&".join(
        f"{_aliyun_percent_encode(k)}={_aliyun_percent_encode(v)}" for k, v in items
    )
    string_to_sign = "POST&" + _aliyun_percent_encode("/") + "&" + _aliyun_percent_encode(canonical)
    sig = hmac.new(
        (secret + "&").encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(sig).decode("ascii")


def _body_encode(params: dict[str, str]) -> str:
    return "&".join(
        f"{quote(str(k), safe='')}={quote(str(v), safe='')}" for k, v in params.items()
    )


def _build_flag_payload(mode: str = "captcha-front", scene_id: str = "") -> str:
    """Inner FLAG-encrypted string: PLATFORM#appName#sceneId#mode#prefix#region"""
    msg = f"{PLATFORM}#{APP_NAME}#{scene_id}#{mode}#{PREFIX}#{REGION}"
    return _aes_encrypt_b64(msg, KEY_FLAG)


def _build_outer_data(flag_enc: str, device_id: str, extra: str = "") -> str:
    """Outer REQ-encrypted Data/DeviceData field."""
    parts = [device_id, DEVICE_TYPE_WEB, flag_enc, APP_VERSION, "CLOUD", extra]
    return _aes_encrypt_b64("#".join(parts), KEY_REQ)


def _build_device_data(scene_id: str = "", mode: str = "captcha-normal") -> str:
    flag = _build_flag_payload(mode=mode, scene_id=scene_id)
    # device_id for captcha DeviceData uses APP_KEY-derived id from capture:
    # first field was 3795d282... which equals device id from FeiLin token
    # For pure HTTP we use a stable pseudo device id
    device_id = _stable_device_id()
    return _build_outer_data(flag, device_id)


def _stable_device_id() -> str:
    """Scene appKey used as first field of DeviceData/Log1 Data and core-id prefix."""
    return APP_KEY


def _cookie_for_data() -> str:
    """Best-effort `_c_WBKFRo` cookie string for pe t7()."""
    try:
        with open(os.path.join(os.path.dirname(__file__), ".zai_credentials.json"), "r") as f:
            return (json.load(f).get("cookie") or "").strip()
    except Exception:
        return ""


def _load_device_identity() -> Optional[dict[str, str]]:
    """
    Known-good (session_key, core_id) pair from a prior successful browser
    enrollment. Fresh Log1/Init pairs fail Verify with F001 because FeiLin
    tokens (fp fields 71/73/78) are only bound during browser enrollment;
    the matched pair keeps returning T001 for new certifyIds.
    """
    path = os.path.join(os.path.dirname(__file__), ".zai_device_identity.json")
    try:
        with open(path, "r") as f:
            ident = json.load(f)
        sk = ident.get("session_key") or ""
        core = ident.get("core_id") or ""
        if len(sk) == 16 and core:
            return {"session_key": sk, "core_id": core}
    except Exception:
        pass
    return None


async def _build_data_field(
    certify_id: str,
    cookie: Optional[str] = None,
    timeout: float = 20.0,
) -> str:
    """
    Build the pe.059 `data` blob (b64) via extracted nv() in Node.

    Returns base64 of the 200+ byte encrypted TrackList payload.
    Raises RuntimeError if the Node builder fails.
    """
    now_ms = int(time.time() * 1000)
    arg = base64.b64encode(secrets.token_bytes(10)).decode("ascii")
    payload = {
        "certifyId": certify_id,
        "TrackList": {
            "mc": "", "tc": "", "mu": "", "te": "", "mp": "",
            "tmv": "", "ks": "", "fi": "", "startTime": now_ms,
        },
        "TrackStartTime": now_ms,
        "VerifyTime": now_ms + 12,
        "arg": arg,
    }
    if cookie is None:
        cookie = _cookie_for_data()
    inp = json.dumps({"payload": payload, "cookie": cookie})
    cli = os.environ.get("NV_DATA_CLI")
    if not cli:
        proj = os.path.join(os.path.dirname(__file__), "js", "nv_data_cli.js")
        cli = proj if os.path.isfile(proj) else "/tmp/nv_data_cli.js"
    if not os.path.isfile(cli):
        raise RuntimeError(f"nv data CLI missing: {cli}")

    loop = asyncio.get_running_loop()
    try:
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["node", cli, inp],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            ),
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"nv data CLI timeout: {e}") from e
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:500]
        raise RuntimeError(f"nv data CLI failed rc={proc.returncode}: {err}")
    try:
        out = json.loads(proc.stdout)
        data = out["data"]
        raw_len = int(out.get("rawLen") or 0)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise RuntimeError(f"nv data CLI bad output: {proc.stdout[:300]!r}") from e
    if not isinstance(data, str) or len(data) < 40:
        raise RuntimeError(f"nv data too short: {data!r}")
    if raw_len and raw_len < 180:
        raise RuntimeError(f"nv data rawLen too small: {raw_len}")
    return data


def _build_device_token(fingerprint_b64: Optional[str] = None) -> str:
    """
    DeviceToken = base64(
      SG_WEB#deviceId-h-timestamp-uuid#fingerprint_b64#counter#md5
    )
    """
    device_id = _stable_device_id()
    ts = int(time.time() * 1000)
    uid = uuid.uuid4().hex
    core = f"{device_id}-h-{ts}-{uid}"

    if fingerprint_b64 is None:
        # Minimal synthetic fingerprint blob (server may accept for TRACELESS)
        fp_raw = hashlib.sha256(f"{device_id}|{ts}".encode()).digest() * 8  # 256 bytes
        fingerprint_b64 = base64.b64encode(fp_raw).decode("ascii")

    counter = "0"
    body = f"SG_WEB#{core}#{fingerprint_b64}#{counter}"
    digest = hashlib.md5(f"{body}#daye,raolewoba!".encode("utf-8")).hexdigest()
    token = f"{body}#{digest}"
    return base64.b64encode(token.encode("utf-8")).decode("ascii")




def _stable_session_key() -> str:
    """Per-process session AES key (16 ASCII hex chars) for fingerprint encryption."""
    # Deterministic per-process so fp encrypt/decrypt stay consistent within a run
    global _SESSION_KEY
    if _SESSION_KEY is None:
        _SESSION_KEY = hashlib.md5(b"zai-glmpp-session-key").hexdigest()[:16]
    return _SESSION_KEY


_SESSION_KEY: Optional[str] = None


FP_FIELDS_TEMPLATE: list[str] = ["W.10054", "", "", "", "", "Linux x86_64", "Firefox", "151.0", "", "", "", "", "", "", "", "", "", "", "", "", "9", "WyYtXDIjKVk=", "", "", "", "", "", "", "", "", "", "", "58793edfc0de3c26c8d665b94a8493ed", "", "12", "", "Linux", "x86_64", "", "", "", "", "104.28.254.180", "10-0|20-1209|11-1217|23-1996|30-1997|40-2011|90-2117|91-2129|92-2130|93-2138|94-2145", "true", "true", "", "768*1366", "", "", "", "", "", "https://chat.z.ai/c/181084d0-aa46-42cb-9634-5889401d52a6", "", "", "", "", "", "", "", "", "", "151.0", "Mozilla/5.0 (X11; Linux x86_64; rv:151.0) Gecko/20100101 Firefox/151.0", "", "", "saf-captcha", "1", "", "", "8JBNeY3FuY90ZamUa2tmZpK4vWxH3eICCwQPeDCX", "1790171363172", "tPom6QSffKRYykgKQ6zLwrauCUdX9rD7QjGxwz570A", "1790171365302", "desktop", "", "ZTHuUbYLhe", "1a0291f4e30838913c7ddfd51ed85d17", "", "5.0 (X11)", "", "", "", "", "0", "0", "1790171364246", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "[]"]



def _build_fingerprint(
    session_key: Optional[str] = None,
    core_id: Optional[str] = None,
    certify_id: str = "",
    ip: str = "104.28.254.180",
    page_url: str = "https://chat.z.ai/",
) -> tuple[str, str]:
    """
    Build FeiLin fingerprint plaintext (111 `#`-fields) and AES-CBC encrypt it.

    Returns (b64_ciphertext, plaintext).
    Key = session_key ASCII (16 bytes), IV = AES_IV.
    Template layout captured from matched browser session (short form with certifyId).
    """
    if session_key is None:
        session_key = _stable_session_key()
    key = session_key.encode("ascii")
    if len(key) != 16:
        raise ValueError("session_key must be 16 ASCII bytes")

    now_ms = int(time.time() * 1000)
    # Exact 111-field layout from matched capture
    parts = list(FP_FIELDS_TEMPLATE)
    # dynamic slots:
    # 43 timings, 53 page URL, 71/73 random tokens, 72/74/87 ms, 77 certifyId
    parts[43] = "10-0|20-1209|11-1217|23-1996|30-1997|40-2011|90-2117|91-2129|92-2130|93-2138|94-2145"
    parts[53] = page_url
    parts[71] = "8JBNeY3FuY90ZamUa2tmZpK4vWxH3eICCwQPeDCX"
    parts[72] = str(now_ms - 2000)
    parts[73] = "tPom6QSffKRYykgKQ6zLwrauCUdX9rD7QjGxwz570A"
    parts[74] = str(now_ms - 100)
    parts[77] = certify_id or ""
    parts[87] = str(now_ms)
    # field 42 IP, 78 hash from capture (static for this env)
    plaintext = "#".join(parts)
    assert plaintext.count("#") == 110

    padder = sym_padding.Pkcs7(128).padder() if hasattr(sym_padding, "Pkcs7") else sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(AES_IV), backend=default_backend()).encryptor()
    ct = enc.update(padded) + enc.finalize()
    return base64.b64encode(ct).decode("ascii"), plaintext


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nonce() -> str:
    return str(uuid.uuid4())


async def _rpc_post(
    session: aiohttp.ClientSession,
    url: str,
    params: dict[str, str],
    secret: str,
) -> dict[str, Any]:
    params = dict(params)
    params["SignatureNonce"] = _nonce()
    params["Signature"] = _sign_rpc(params, secret)
    body = _body_encode(params)
    async with session.post(
        url,
        data=body,
        headers=CAPTCHA_HEADERS,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        text = await resp.text()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text, "_status": resp.status}


async def _log_device_register(
    session: aiohttp.ClientSession,
    device_token: str,
    device_config: Optional[str] = None,
) -> dict[str, Any]:
    """Log1 (register) -> returns DeviceConfig; Log2/Log3 best-effort."""
    device_id = _stable_device_id()

    # Log1: device register, empty scene
    flag = _build_flag_payload(mode="captcha-front", scene_id="")
    data = _build_outer_data(flag, device_id)
    log1 = await _rpc_post(
        session,
        CLOUDAUTH_URL,
        {
            "AaduaneId": DEVICE_KEY_ID,
            "Version": API_VERSION_DEVICE,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "Format": "JSON",
            "Action": "Log1",
            "Data": data,
        },
        DEVICE_KEY_SECRET,
    )

    cfg = None
    if isinstance(log1, dict):
        ro = log1.get("ResultObject") or {}
        if isinstance(ro, dict):
            cfg = ro.get("DeviceConfig")

    # Log2/Log3: upload-style payload (UPLOAD key outer); best-effort
    # Reuse structure from capture: outer UPLOAD with nested fields
    # If we don't have full browser fingerprint chain, still send a minimal payload.
    if device_config or cfg:
        # minimal outer using UPLOAD key if possible; otherwise skip
        pass

    # Best-effort Log2/Log3 with simple outer data (some servers accept)
    simple = _aes_encrypt_b64(
        f"{device_id}#{DEVICE_TYPE_WEB}#{flag}#{APP_VERSION}#CLOUD#",
        KEY_UPLOAD,
    )
    for action in ("Log2", "Log3"):
        try:
            await _rpc_post(
                session,
                CLOUDAUTH_URL,
                {
                    "AaduaneId": DEVICE_KEY_ID,
                    "Version": API_VERSION_DEVICE,
                    "SignatureMethod": "HMAC-SHA1",
                    "SignatureVersion": "1.0",
                    "Format": "JSON",
                    "Action": action,
                    "Data": simple,
                },
                DEVICE_KEY_SECRET,
            )
        except Exception:
            pass

    return {"device_config": cfg or device_config, "log1": log1}


async def get_captcha_verify_param(
    session: Optional[aiohttp.ClientSession] = None,
    device_token: Optional[str] = None,
    data_field: Optional[str] = None,
) -> str:
    """
    Full pure-HTTP TRACELESS captcha flow.

    Log1 -> DeviceConfig (session key + core) -> fingerprint encrypt ->
    InitCaptchaV3 -> VerifyCaptchaV3 -> base64 captcha_verify_param.

    `data_field` is the pe-built binary blob (b64). Until its algorithm is
    reversed, pass a fresh browser-captured value or expect F002.
    Raises RuntimeError on failure.
    """
    own = session is None
    if own:
        session = aiohttp.ClientSession()
    try:
        # 1) Device register -> DeviceConfig (session_key, core_id)
        flag = _build_flag_payload(mode="captcha-front", scene_id="")
        log1_data = _build_outer_data(flag, _stable_device_id())
        log1 = await _rpc_post(
            session,
            CLOUDAUTH_URL,
            {
                "AaduaneId": DEVICE_KEY_ID,
                "Version": API_VERSION_DEVICE,
                "SignatureMethod": "HMAC-SHA1",
                "SignatureVersion": "1.0",
                "Format": "JSON",
                "Action": "Log1",
                "Data": log1_data,
            },
            DEVICE_KEY_SECRET,
        )
        cfg = (((log1 or {}).get("ResultObject") or {}).get("DeviceConfig"))
        session_key: Optional[str] = None
        core_id: Optional[str] = None
        if cfg:
            try:
                dc_fields = _aes_decrypt_b64(cfg, KEY_RES).decode("utf-8").split("#")
                raw_sk = dc_fields[0]
                pad = "=" * (-len(raw_sk) % 4)
                session_key = base64.b64decode(raw_sk + pad).decode("ascii")
                core_id = dc_fields[2]
            except Exception:
                session_key = None

        # Prefer known-good device identity (browser-enrolled FeiLin tokens).
        # Fresh Log1/Init DeviceConfig pairs return F001 on Verify.
        known = _load_device_identity()
        if known:
            session_key = known["session_key"]
            core_id = known["core_id"]

        # 2) InitCaptchaV3 with DeviceData
        device_data = _build_device_data(scene_id=SCENE_ID, mode="captcha-normal")
        init_params: dict[str, str] = {
            "AaduaneId": CAPTCHA_KEY_ID,
            "Version": API_VERSION_CAPTCHA,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "Format": "JSON",
            "Timestamp": _timestamp_utc(),
            "Action": "InitCaptchaV3",
            "SceneId": SCENE_ID,
            "Language": "en",
            "Mode": "popup",
            "UpLang": "true",
            "DeviceData": device_data,
        }
        init = await _rpc_post(session, CAPTCHA_URL, init_params, CAPTCHA_KEY_SECRET)
        if not isinstance(init, dict) or not init.get("CertifyId"):
            init_params.pop("DeviceData", None)
            init_params["DeviceToken"] = device_token or _build_device_token()
            init_params["Timestamp"] = _timestamp_utc()
            init = await _rpc_post(session, CAPTCHA_URL, init_params, CAPTCHA_KEY_SECRET)

        certify_id = (init or {}).get("CertifyId")
        captcha_type = (init or {}).get("CaptchaType", "")
        if not certify_id:
            raise RuntimeError(f"InitCaptchaV3 failed: {init}")

        # Prefer DeviceConfig from Init if Log1 did not yield one
        if not session_key:
            idc = (init or {}).get("DeviceConfig")
            if idc:
                try:
                    dc_fields = _aes_decrypt_b64(idc, KEY_RES).decode("utf-8").split("#")
                    raw_sk = dc_fields[0]
                    pad = "=" * (-len(raw_sk) % 4)
                    session_key = base64.b64decode(raw_sk + pad).decode("ascii")
                    core_id = dc_fields[2]
                except Exception:
                    pass

        # 3) Fingerprint + deviceToken bound to this certifyId
        if session_key and core_id and len(session_key) == 16:
            fp_b64, _fp_pt = _build_fingerprint(
                session_key=session_key,
                core_id=core_id,
                certify_id=certify_id,
                page_url="https://chat.z.ai/",
            )
            body = f"SG_WEB#{core_id}#{fp_b64}#0"
            digest = hashlib.md5((body + "#daye,raolewoba!").encode("utf-8")).hexdigest()
            device_token = base64.b64encode((body + "#" + digest).encode("utf-8")).decode("ascii")
        else:
            fp_b64, _fp_pt = _build_fingerprint(certify_id=certify_id)
            device_token = device_token or _build_device_token(fp_b64)

        # 4) data blob via extracted pe.059 nv() (pure Node, no browser)
        if data_field is None:
            try:
                data_field = await _build_data_field(certify_id)
            except Exception as e:
                raise RuntimeError(f"data field build failed: {e}") from e

        # 5) VerifyCaptchaV3 — CaptchaVerifyParam is RAW JSON (not base64)
        cvp_obj: dict[str, Any] = {
            "sceneId": SCENE_ID,
            "certifyId": certify_id,
            "deviceToken": device_token,
        }
        if data_field:
            cvp_obj["data"] = data_field
        verify_param = json.dumps(cvp_obj, separators=(",", ":"))
        verify = await _rpc_post(
            session,
            CAPTCHA_VERIFY_URL,
            {
                "AaduaneId": CAPTCHA_KEY_ID,
                "Version": API_VERSION_CAPTCHA,
                "SignatureMethod": "HMAC-SHA1",
                "SignatureVersion": "1.0",
                "Format": "JSON",
                "Timestamp": _timestamp_utc(),
                "Action": "VerifyCaptchaV3",
                "SceneId": SCENE_ID,
                "CertifyId": certify_id,
                "CaptchaVerifyParam": verify_param,
            },
            CAPTCHA_KEY_SECRET,
        )

        result = ((verify or {}).get("Result") or {}) if isinstance(verify, dict) else {}
        security_token = result.get("securityToken")
        verify_ok = bool(result.get("VerifyResult")) or (
            str(result.get("VerifyCode", "")) == "T001"
        )

        if not security_token or not verify_ok:
            raise RuntimeError(
                f"VerifyCaptchaV3 failed (type={captcha_type}, "
                f"code={result.get('VerifyCode')}): {verify}"
            )

        payload = {
            "certifyId": certify_id,
            "sceneId": SCENE_ID,
            "isSign": True,
            "securityToken": security_token,
        }
        return base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
    finally:
        if own:
            await session.close()


async def _main() -> None:
    param = await get_captcha_verify_param()
    print("captcha_verify_param:", param[:80] + "...")
    # decode for verification
    pad = "=" * (-len(param) % 4)
    print("decoded:", json.loads(base64.b64decode(param + pad)))


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
