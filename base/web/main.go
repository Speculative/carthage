// Container-local ingress. Authentication is enforced by Lyoko's Traefik.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const ostrakaPort = 8090

func handler(host string) http.Handler {
	return handlerWithPort(host, ostrakaPort)
}

func handlerWithPort(host string, webPort int) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host == host && r.URL.Path == "/.carthage/ports" {
			w.Header().Set("Cache-Control", "no-store")
			w.Header().Set("Content-Type", "application/json")
			if r.Method != http.MethodGet {
				w.Header().Set("Allow", "GET")
				w.WriteHeader(http.StatusMethodNotAllowed)
				return
			}
			ports, err := listeningPorts()
			if err != nil {
				http.Error(w, "port discovery unavailable", http.StatusServiceUnavailable)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"ports": ports})
			return
		}
		port := webPort
		isOstraka := r.Host == host
		if !isOstraka {
			label, ok := strings.CutSuffix(r.Host, "."+host)
			n, err := strconv.Atoi(label)
			if !ok || err != nil || strconv.Itoa(n) != label || n < 1024 || n > 65535 {
				http.Error(w, "unknown container endpoint", http.StatusNotFound)
				return
			}
			port = n
		}
		// Ostraka binds to loopback and checks Host and Origin. Validate the
		// external origin before translating both headers for that listener.
		if isOstraka || port == webPort {
			if origin := r.Header.Get("Origin"); origin != "" {
				u, err := url.Parse(origin)
				if err != nil || u.Host != r.Host || u.Scheme != "https" || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || u.User != nil {
					http.Error(w, "cross-origin request rejected", http.StatusForbidden)
					return
				}
				r.Header.Set("Origin", fmt.Sprintf("http://127.0.0.1:%d", port))
			}
		}
		target := &url.URL{Scheme: "http", Host: net.JoinHostPort("127.0.0.1", strconv.Itoa(port))}
		proxy := httputil.NewSingleHostReverseProxy(target)
		proxy.Transport = &http.Transport{DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			dialer := &net.Dialer{Timeout: 5 * time.Second}
			conn, err := dialer.DialContext(ctx, network, address)
			if err == nil {
				return conn, nil
			}
			// IPv6-only localhost listeners are also reachable via port URLs.
			return dialer.DialContext(ctx, network, net.JoinHostPort("::1", strconv.Itoa(port)))
		}, ResponseHeaderTimeout: 30 * time.Second}
		defer proxy.Transport.(*http.Transport).CloseIdleConnections()
		// Local dev servers often allow only localhost Host headers. Keep the
		// browser hostname available as forwarding metadata. WebSockets retain Origin.
		r.Header.Set("X-Forwarded-Host", r.Host)
		r.Host = target.Host
		proxy.ServeHTTP(w, r)
	})
}

func ensureOstraka(ctx context.Context) error {
	info, err := os.Stat(".ostraka")
	if err == nil {
		if !info.IsDir() {
			return fmt.Errorf(".ostraka exists but is not a directory")
		}
		return nil
	}
	if !os.IsNotExist(err) {
		return err
	}
	cmd := exec.CommandContext(ctx, "ostraka", "init")
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("initialize Ostraka workspace: %w", err)
	}
	return nil
}

func run() error {
	host := os.Getenv("CARTHAGE_WEB_HOST")
	if host == "" {
		return fmt.Errorf("CARTHAGE_WEB_HOST is required")
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	if err := ensureOstraka(ctx); err != nil {
		return err
	}
	socket := "/run/carthage-web/router.sock"
	if err := os.Remove(socket); err != nil && !os.IsNotExist(err) {
		return err
	}
	listener, err := net.Listen("unix", socket)
	if err != nil {
		return err
	}
	defer listener.Close()
	server := &http.Server{Handler: handler(host), ReadHeaderTimeout: 5 * time.Second}
	errors := make(chan error, 3)
	go func() { errors <- server.Serve(listener) }()
	defer server.Close()
	var children []*exec.Cmd
	defer func() {
		stop()
		for _, cmd := range children {
			_ = cmd.Process.Kill()
		}
	}()
	webArgs := []string{"web", "--listen", fmt.Sprintf("127.0.0.1:%d", ostrakaPort)}
	if os.Getenv("CARTHAGE_WEB_SUPERVISE") == "1" {
		webArgs = append(webArgs, "--supervise")
	}
	cmd := exec.CommandContext(ctx, "ostraka", webArgs...)
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	if err := cmd.Start(); err != nil {
		return err
	}
	children = append(children, cmd)
	go func() { errors <- cmd.Wait() }()

	select {
	case <-ctx.Done():
		return nil
	case err := <-errors:
		return fmt.Errorf("web service stopped: %v", err)
	}
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}
