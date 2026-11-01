package main

import (
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
)

func TestRoutingBoundary(t *testing.T) {
	h := handler("demo.agents.example.com")
	for _, host := range []string{"evil.example.com", "80.demo.agents.example.com", "65536.demo.agents.example.com", "018090.demo.agents.example.com", "a.demo.agents.example.com", "5173.other.agents.example.com"} {
		r := httptest.NewRequest("GET", "http://"+host+"/", nil)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != 404 {
			t.Errorf("%s: got %d", host, w.Code)
		}
	}
	r := httptest.NewRequest("POST", "https://demo.agents.example.com/api/items", nil)
	r.Header.Set("Origin", "https://evil.example.com")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 403 {
		t.Fatalf("cross origin: %d", w.Code)
	}
}

func TestDevProxyUsesLocalHostAndStreams(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Seen-Host", r.Host)
		io.WriteString(w, "dev server")
	}))
	defer backend.Close()
	_, port, _ := net.SplitHostPort(backend.Listener.Addr().String())
	host := port + ".demo.agents.example.com"
	r := httptest.NewRequest("GET", "https://"+host+"/", nil)
	w := httptest.NewRecorder()
	handler("demo.agents.example.com").ServeHTTP(w, r)
	if w.Code != 200 || w.Body.String() != "dev server" || w.Header().Get("Seen-Host") != "127.0.0.1:"+port {
		t.Fatalf("proxy response: %v %s", w.Result(), w.Body.String())
	}
}

func TestOstrakaOriginTranslation(t *testing.T) {
	var address string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != address || r.Header.Get("Origin") != "http://"+address {
			t.Errorf("untranslated request: %s %s", r.Host, r.Header.Get("Origin"))
		}
		w.WriteHeader(204)
	}))
	defer backend.Close()
	address = backend.Listener.Addr().String()
	_, portText, _ := net.SplitHostPort(address)
	port, _ := strconv.Atoi(portText)
	r := httptest.NewRequest("POST", "https://demo.agents.example.com/api/items", nil)
	r.Header.Set("Origin", "https://demo.agents.example.com")
	w := httptest.NewRecorder()
	handlerWithPort("demo.agents.example.com", port).ServeHTTP(w, r)
	if w.Code != 204 {
		t.Fatalf("response: %d", w.Code)
	}
}

func TestWebSocketUpgrade(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			return
		}
		defer conn.Close()
		io.WriteString(conn, "HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\nping")
	}))
	defer backend.Close()
	_, port, _ := net.SplitHostPort(backend.Listener.Addr().String())
	gateway := httptest.NewServer(handler("demo.agents.example.com"))
	defer gateway.Close()
	req, _ := http.NewRequest("GET", gateway.URL, nil)
	req.Host = port + ".demo.agents.example.com"
	req.Header.Set("Connection", "Upgrade")
	req.Header.Set("Upgrade", "websocket")
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	payload := make([]byte, 4)
	_, err = io.ReadFull(response.Body, payload)
	if err != nil || response.StatusCode != 101 || string(payload) != "ping" {
		t.Fatalf("upgrade: %v %s", err, payload)
	}
}
