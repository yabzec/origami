package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
)

func TestWsURL(t *testing.T) {
	got, _ := wsURL("https://origami.example.org", "a b")
	if got != "wss://origami.example.org/api/agent/ws?token=a+b" {
		t.Fatalf("got %s", got)
	}
	got, _ = wsURL("http://localhost:8000/", "t")
	if got != "ws://localhost:8000/api/agent/ws?token=t" {
		t.Fatalf("got %s", got)
	}
}

func TestAgentSessionServesEsclRequest(t *testing.T) {
	scannerSrv, _ := fakeScanner(t)
	tokens := make(chan string, 4)
	results := make(chan []any, 1)

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tokens <- r.URL.Query().Get("token")
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer c.CloseNow()
		ctx := r.Context()
		_ = writeJSON(ctx, c, map[string]any{"type": "welcome", "resume_token": "resume-1"})
		var got []any
		for len(got) < 2 { // hello + devices
			_, data, err := c.Read(ctx)
			if err != nil {
				return
			}
			var m map[string]any
			_ = json.Unmarshal(data, &m)
			got = append(got, m["type"])
		}
		_ = writeJSON(ctx, c, map[string]any{"type": "escl", "id": "0123456789abcdef",
			"scanner_uuid": "u1", "method": "GET", "path": "ScanJobs/7/NextDocument"})
		for {
			typ, data, err := c.Read(ctx)
			if err != nil {
				return
			}
			if typ == websocket.MessageBinary {
				got = append(got, string(data))
				continue
			}
			var m map[string]any
			_ = json.Unmarshal(data, &m)
			got = append(got, m["type"])
			if m["type"] == "end" {
				results <- got
				return
			}
		}
	}))
	defer srv.Close()

	sc := Scanner{UUID: "u1", Name: "HP", BaseURL: scannerSrv.URL + "/eSCL"}
	browse := func(ctx context.Context, d time.Duration) []Scanner { return []Scanner{sc} }
	a := NewAgent(srv.URL, "launch-1", browse)
	a.exec.Client = scannerSrv.Client()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	go func() { _ = a.Run(ctx) }()

	select {
	case got := <-results:
		joined := strings.Join(toStrings(got), ",")
		want := "hello,devices,escl_response,0123456789abcdefJPEGDATA,end"
		if joined != want {
			t.Fatalf("got %s want %s", joined, want)
		}
	case <-ctx.Done():
		t.Fatal("timeout")
	}
	if <-tokens != "launch-1" {
		t.Fatal("first connect must use the launch token")
	}
	// after the server closes, the agent reconnects with the resume token
	select {
	case tok := <-tokens:
		if tok != "resume-1" {
			t.Fatalf("reconnect used %q", tok)
		}
	case <-ctx.Done():
		t.Fatal("no reconnect")
	}
}

func toStrings(xs []any) []string {
	out := make([]string, len(xs))
	for i, x := range xs {
		out[i], _ = x.(string)
	}
	return out
}

func TestAgentStopsOnRejectedToken(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		_ = c.Close(websocket.StatusCode(4401), "bad token")
	}))
	defer srv.Close()
	browse := func(ctx context.Context, d time.Duration) []Scanner { return nil }
	a := NewAgent(srv.URL, "bad", browse)
	done := make(chan error, 1)
	go func() { done <- a.Run(context.Background()) }()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("want error")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Run kept retrying after 4401")
	}
	if hits.Load() != 1 {
		t.Fatalf("hits=%d", hits.Load())
	}
}

func TestAgentIgnoresBadIDAndEmptyWelcome(t *testing.T) {
	scannerSrv, _ := fakeScanner(t)
	got := make(chan string, 4)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got <- r.URL.Query().Get("token")
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer c.CloseNow()
		ctx := r.Context()
		_ = writeJSON(ctx, c, map[string]any{"type": "welcome", "resume_token": ""})
		_ = writeJSON(ctx, c, map[string]any{"type": "escl", "id": "short",
			"scanner_uuid": "u1", "method": "GET", "path": "ScannerStatus"})
		time.Sleep(300 * time.Millisecond)
		c.Close(websocket.StatusNormalClosure, "")
	}))
	defer srv.Close()
	sc := Scanner{UUID: "u1", Name: "HP", BaseURL: scannerSrv.URL + "/eSCL"}
	a := NewAgent(srv.URL, "launch-1", func(ctx context.Context, d time.Duration) []Scanner { return []Scanner{sc} })
	ctx, cancel := context.WithTimeout(context.Background(), 6*time.Second)
	defer cancel()
	go func() { _ = a.Run(ctx) }()
	<-got
	select {
	case tok := <-got:
		if tok != "launch-1" {
			t.Fatalf("empty welcome replaced token: %q", tok)
		}
	case <-ctx.Done():
		t.Fatal("no reconnect")
	}
}
