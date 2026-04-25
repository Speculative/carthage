"""Minimal HTTP server used by E2E test A."""
import http.server
import socketserver


if __name__ == "__main__":
    with socketserver.TCPServer(("0.0.0.0", 8000), http.server.SimpleHTTPRequestHandler) as httpd:
        print("serving on 0.0.0.0:8000", flush=True)
        httpd.serve_forever()
