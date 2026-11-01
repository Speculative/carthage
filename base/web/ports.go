package main

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"sort"
	"strconv"
	"strings"
)

// Read kernel listener tables rather than probing every possible port. Only
// addresses reachable through the local router are included.
func parseListeningPorts(r io.Reader, ports map[int]bool) error {
	scanner := bufio.NewScanner(r)
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 4 || fields[3] != "0A" {
			continue
		}
		addr, hexPort, ok := strings.Cut(fields[1], ":")
		if !ok {
			continue
		}
		switch addr {
		case "00000000", "0100007F", "00000000000000000000000000000000", "00000000000000000000000001000000":
		default:
			continue
		}
		port, err := strconv.ParseInt(hexPort, 16, 32)
		if err == nil && port >= 1024 && port <= 65535 && port != ostrakaPort {
			ports[int(port)] = true
		}
	}
	return scanner.Err()
}

func listeningPorts() ([]int, error) {
	ports := map[int]bool{}
	for _, path := range []string{"/proc/net/tcp", "/proc/net/tcp6"} {
		f, err := os.Open(path)
		if os.IsNotExist(err) && strings.HasSuffix(path, "tcp6") {
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("read listeners: %w", err)
		}
		err = parseListeningPorts(f, ports)
		f.Close()
		if err != nil {
			return nil, err
		}
	}
	result := make([]int, 0, len(ports))
	for port := range ports {
		result = append(result, port)
	}
	sort.Ints(result)
	return result, nil
}
