import argparse
import ipaddress
import os
import secrets
import socket
import threading
import webbrowser

import uvicorn


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def lan_address(host: str) -> str:
    if host not in ("0.0.0.0", "::"):
        return host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            return probe.getsockname()[0]
    except OSError:
        return socket.gethostname()


def url_for(host: str, port: int) -> str:
    host = host.strip("[]")
    return f"http://{f'[{host}]' if ':' in host else host}:{port}"


def main() -> None:
    parser = argparse.ArgumentParser(prog="spectra", description="Spectra voice cloning WebUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7433)
    parser.add_argument("--open", action="store_true", help="Open the WebUI in the default browser")
    args = parser.parse_args()
    local_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = url_for(local_host, args.port)
    print(f"Spectra is starting on {url}  (Ctrl + C to stop)", flush=True)
    open_url = url
    if not is_loopback(args.host):
        token = os.environ.get("SPECTRA_TOKEN") or secrets.token_urlsafe(24)
        os.environ["SPECTRA_TOKEN"] = token
        print("Access from other devices requires a token. Open this URL on them once:", flush=True)
        print(f"  {url_for(lan_address(args.host), args.port)}/?token={token}", flush=True)
        if not is_loopback(local_host):
            open_url = f"{url}/?token={token}"
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(open_url)).start()
    loop = "asyncio:SelectorEventLoop" if os.name == "nt" else "auto"
    uvicorn.run("spectra.server:app", host=args.host, port=args.port, log_level="warning", loop=loop)


if __name__ == "__main__":
    main()
