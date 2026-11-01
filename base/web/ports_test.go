package main

import (
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
)

func TestListeningPortParsing(t *testing.T) {
	input := `  sl local_address rem_address st
0: 0100007F:1435 00000000:0000 0A
1: 00000000:1435 00000000:0000 0A
2: 0100007F:1F9A 00000000:0000 0A
3: 0100007F:0050 00000000:0000 0A
4: 0100007F:1436 00000000:0000 01
5: 020011AC:1437 00000000:0000 0A
6: 00000000000000000000000001000000:1438 00000000000000000000000000000000:0000 0A
7: nonsense:xyz 0 0A
`
	ports := map[int]bool{}
	if err := parseListeningPorts(strings.NewReader(input), ports); err != nil {
		t.Fatal(err)
	}
	if len(ports) != 2 || !ports[5173] || !ports[5176] {
		t.Fatalf("unexpected listeners: %v", ports)
	}
}
func TestDiscoveryIncludesLiveListener(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer backend.Close()
	_, portText, _ := net.SplitHostPort(backend.Listener.Addr().String())
	port, _ := strconv.Atoi(portText)
	r := httptest.NewRequest("GET", "https://demo.agents.example.com/.carthage/ports", nil)
	w := httptest.NewRecorder()
	handler("demo.agents.example.com").ServeHTTP(w, r)
	var data struct {
		Ports []int `json:"ports"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &data); err != nil {
		t.Fatal(err)
	}
	for _, p := range data.Ports {
		if p == port {
			return
		}
	}
	t.Fatalf("listener %d missing from %v", port, data.Ports)
}

func TestIPv6OnlyListenerIsProxied(t *testing.T) {
	ln, err := net.Listen("tcp6", "[::1]:0")
	if err != nil {
		t.Skip("IPv6 unavailable")
	}
	backend := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
	backend.Listener.Close()
	backend.Listener = ln
	backend.Start()
	defer backend.Close()
	_, port, _ := net.SplitHostPort(ln.Addr().String())
	r := httptest.NewRequest("GET", "https://"+port+".demo.agents.example.com/", nil)
	w := httptest.NewRecorder()
	handler("demo.agents.example.com").ServeHTTP(w, r)
	if w.Code != 204 {
		t.Fatalf("IPv6 listener: %d %s", w.Code, w.Body.String())
	}
}
