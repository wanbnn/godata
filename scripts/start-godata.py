from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


INSTALL_DIR = Path(__file__).resolve().parent.parent
PYTHON_EXE = INSTALL_DIR / ".venv" / "Scripts" / "python.exe"
CLOUDFLARED_EXE = INSTALL_DIR / "bin" / "cloudflared.exe"
ENV_FILE = INSTALL_DIR / ".env"
LOG_DIR = INSTALL_DIR / "logs"
CREATE_NEW_PROCESS_GROUP = 0x00000200


def read_api_key() -> str:
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("GODATA_API_KEY="):
            key = line.partition("=")[2].strip()
            if key:
                return key
    raise RuntimeError(f"GODATA_API_KEY não encontrada em {ENV_FILE}")


def wait_for_health(app: subprocess.Popen[bytes]) -> None:
    for _ in range(60):
        if app.poll() is not None:
            raise RuntimeError(f"GoData encerrou durante a inicialização (código {app.returncode}).")
        try:
            with urllib.request.urlopen("http://127.0.0.1:4400/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("GoData não respondeu em http://127.0.0.1:4400/health.")


def wait_for_tunnel_url(tunnel: subprocess.Popen[bytes], *logs: Path) -> str:
    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    for _ in range(90):
        if tunnel.poll() is not None:
            break
        output = "\n".join(
            path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            for path in logs
        )
        match = pattern.search(output)
        if match:
            return match.group(0)
        time.sleep(1)
    raise RuntimeError(f"cloudflared não publicou uma URL. Consulte {logs[-1]}")


def terminate(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> int:
    username = os.environ.get("USERNAME", "usuario")
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\GoData-{username}")
    if not mutex:
        raise ctypes.WinError()
    if ctypes.windll.kernel32.GetLastError() == 183:
        print("O GoData já está em execução para este usuário.")
        return 0

    app = None
    tunnel = None
    tunnel_out_handle = None
    tunnel_err_handle = None
    try:
        for required in (PYTHON_EXE, CLOUDFLARED_EXE, ENV_FILE):
            if not required.exists():
                raise RuntimeError(f"Arquivo obrigatório não encontrado: {required}")

        api_key = read_api_key()
        app_environment = os.environ.copy()
        for name in tuple(app_environment):
            if name.startswith("GODATA_"):
                del app_environment[name]
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        tunnel_out = LOG_DIR / "cloudflared.out.log"
        tunnel_err = LOG_DIR / "cloudflared.err.log"
        tunnel_out_handle = tunnel_out.open("wb")
        tunnel_err_handle = tunnel_err.open("wb")

        app = subprocess.Popen(
            [
                str(PYTHON_EXE), "-m", "uvicorn", "godata.main:app",
                "--app-dir", str(INSTALL_DIR / "src"),
                "--host", "127.0.0.1", "--port", "4400",
            ],
            cwd=INSTALL_DIR,
            env=app_environment,
            creationflags=CREATE_NEW_PROCESS_GROUP,
        )
        wait_for_health(app)

        tunnel = subprocess.Popen(
            [
                str(CLOUDFLARED_EXE), "tunnel", "--no-autoupdate",
                "--url", "http://127.0.0.1:4400",
            ],
            cwd=INSTALL_DIR,
            stdout=tunnel_out_handle,
            stderr=tunnel_err_handle,
            creationflags=CREATE_NEW_PROCESS_GROUP,
        )
        tunnel_url = wait_for_tunnel_url(tunnel, tunnel_out, tunnel_err)

        print("=" * 60)
        print(" GoData iniciado com Cloudflare Tunnel")
        print("=" * 60)
        print(f"URL:       {tunnel_url}")
        print(f"Swagger:   {tunnel_url}/docs")
        print(f"API Key:   {api_key}")
        print("\nMantenha esta janela aberta. Pressione Ctrl+C para encerrar.")
        return tunnel.wait()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"\nFalha ao iniciar o GoData: {exc}", file=sys.stderr)
        input("Pressione Enter para fechar.")
        return 1
    finally:
        terminate(tunnel)
        terminate(app)
        if tunnel_out_handle:
            tunnel_out_handle.close()
        if tunnel_err_handle:
            tunnel_err_handle.close()
        ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
