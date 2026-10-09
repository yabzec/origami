package main

import (
	"context"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"

	"github.com/libp2p/zeroconf/v2"
)

// Browser finds eSCL scanners; tests replace it.
type Browser func(ctx context.Context, timeout time.Duration) []Scanner

func scannerFromRecord(instance string, txt []string, ipv4, ipv6 []net.IP, port int, secure bool) (Scanner, bool) {
	fields := map[string]string{}
	for _, kv := range txt {
		if k, v, ok := strings.Cut(kv, "="); ok {
			fields[strings.ToLower(k)] = v
		}
	}
	var host string
	switch {
	case len(ipv4) > 0:
		host = ipv4[0].String()
	case len(ipv6) > 0:
		host = "[" + ipv6[0].String() + "]"
	default:
		return Scanner{}, false
	}
	scheme := "http"
	if secure {
		scheme = "https"
	}
	root := strings.Trim(fields["rs"], "/")
	if root == "" {
		root = "eSCL"
	}
	uuid := fields["uuid"]
	if uuid == "" {
		uuid = instance
	}
	name := fields["ty"]
	if name == "" {
		name = instance
	}
	return Scanner{UUID: uuid, Name: name, BaseURL: fmt.Sprintf("%s://%s:%d/%s", scheme, host, port, root)}, true
}

func browseMDNS(ctx context.Context, timeout time.Duration) []Scanner {
	var (
		mu   sync.Mutex
		out  []Scanner
		seen = map[string]bool{}
		wg   sync.WaitGroup
	)
	for _, svc := range []string{"_uscan._tcp", "_uscans._tcp"} {
		secure := svc == "_uscans._tcp"
		entries := make(chan *zeroconf.ServiceEntry, 8)
		bctx, cancel := context.WithTimeout(ctx, timeout)
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-bctx.Done(): // do not rely on Browse closing the channel
					return
				case e, open := <-entries:
					if !open {
						return
					}
					sc, ok := scannerFromRecord(e.Instance, e.Text, e.AddrIPv4, e.AddrIPv6, e.Port, secure)
					mu.Lock()
					if ok && !seen[sc.UUID] {
						seen[sc.UUID] = true
						out = append(out, sc)
					}
					mu.Unlock()
				}
			}
		}()
		go func() {
			defer cancel()
			_ = zeroconf.Browse(bctx, svc, "local.", entries) // closes entries when bctx ends
		}()
	}
	wg.Wait()
	return out
}
