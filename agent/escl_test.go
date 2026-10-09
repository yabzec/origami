package main

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
)

// hitLog records requests seen by a fake server; safe for concurrent use.
type hitLog struct {
	mu   sync.Mutex
	hits []string
}

func (h *hitLog) add(s string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.hits = append(h.hits, s)
}

func (h *hitLog) all() []string {
	h.mu.Lock()
	defer h.mu.Unlock()
	return append([]string(nil), h.hits...)
}

func fakeScanner(t *testing.T) (*httptest.Server, *hitLog) {
	log := &hitLog{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		log.add(r.Method + " " + r.URL.Path + " " + string(body))
		switch r.URL.Path {
		case "/eSCL/ScanJobs":
			w.Header().Set("Location", "http://"+r.Host+"/eSCL/ScanJobs/7")
			w.WriteHeader(http.StatusCreated)
		case "/eSCL/ScanJobs/7/NextDocument":
			w.Header().Set("Content-Type", "image/jpeg")
			_, _ = w.Write([]byte("JPEGDATA"))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, log
}

func executorFor(srv *httptest.Server) *Executor {
	sc := Scanner{UUID: "u1", Name: "HP", BaseURL: srv.URL + "/eSCL"}
	return &Executor{Client: srv.Client(), Lookup: func(uuid string) (Scanner, bool) {
		return sc, uuid == sc.UUID
	}}
}

func TestDoPostsAndRewritesLocation(t *testing.T) {
	srv, log := fakeScanner(t)
	res := executorFor(srv).Do(context.Background(), "u1", "POST", "ScanJobs", "<xml/>")
	if res.Status != 201 {
		t.Fatalf("status %d", res.Status)
	}
	if res.Headers["Location"] != "ScanJobs/7" {
		t.Fatalf("location %q", res.Headers["Location"])
	}
	if got := log.all(); got[0] != "POST /eSCL/ScanJobs <xml/>" {
		t.Fatalf("hit %q", got[0])
	}
}

func TestDoReturnsBody(t *testing.T) {
	srv, _ := fakeScanner(t)
	res := executorFor(srv).Do(context.Background(), "u1", "GET", "ScanJobs/7/NextDocument", "")
	if res.Status != 200 || string(res.Body) != "JPEGDATA" || res.ContentType != "image/jpeg" {
		t.Fatalf("bad result %+v", res)
	}
}

func TestDoRejectsWithoutCallingScanner(t *testing.T) {
	srv, log := fakeScanner(t)
	ex := executorFor(srv)
	cases := []struct{ uuid, method, path string }{
		{"other", "GET", "ScannerCapabilities"},
		{"u1", "PUT", "ScanJobs"},
		{"u1", "GET", "/etc/passwd"},
		{"u1", "GET", "../admin"},
		{"u1", "GET", "ScanJobs/../../admin"},
		{"u1", "GET", "http://10.0.0.1/x"},
		{"u1", "GET", ""},
	}
	for _, c := range cases {
		if res := ex.Do(context.Background(), c.uuid, c.method, c.path, ""); res.Status != 403 {
			t.Errorf("%+v: want 403, got %d", c, res.Status)
		}
	}
	if got := log.all(); len(got) != 0 {
		t.Fatalf("scanner was called: %v", got)
	}
}

func TestRelativeLocation(t *testing.T) {
	base := "http://192.168.1.20:80/eSCL"
	cases := map[string]string{
		"http://192.168.1.20/eSCL/ScanJobs/7":           "ScanJobs/7",
		"/eSCL/ScanJobs/7":                              "ScanJobs/7",
		"ScanJobs/7":                                    "ScanJobs/7",
		"https://192.168.1.20:443/eSCL/ScanJobs/abc-1/": "ScanJobs/abc-1",
	}
	for in, want := range cases {
		if got := relativeLocation(base, in); got != want {
			t.Errorf("%s: got %q want %q", in, got, want)
		}
	}
}

func TestDoDoesNotFollowRedirects(t *testing.T) {
	second, secondLog := fakeScanner(t)
	first := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Location", second.URL+"/eSCL/ScanJobs/7")
		w.WriteHeader(http.StatusFound)
	}))
	t.Cleanup(first.Close)
	res := executorFor(first).Do(context.Background(), "u1", "GET", "ScanJobs/7", "")
	if res.Status != http.StatusFound {
		t.Fatalf("want 302 returned as-is, got %d", res.Status)
	}
	if got := secondLog.all(); len(got) != 0 {
		t.Fatalf("redirect target was called: %v", got)
	}
}

func TestDoRejectsOversizeBody(t *testing.T) {
	old := maxBody
	maxBody = 4
	t.Cleanup(func() { maxBody = old })
	srv, _ := fakeScanner(t)
	res := executorFor(srv).Do(context.Background(), "u1", "GET", "ScanJobs/7/NextDocument", "")
	if res.Status != http.StatusBadGateway || res.Body != nil {
		t.Fatalf("oversize body: want 502 without body, got %d len %d", res.Status, len(res.Body))
	}
	maxBody = 8
	res = executorFor(srv).Do(context.Background(), "u1", "GET", "ScanJobs/7/NextDocument", "")
	if res.Status != 200 || !bytes.Equal(res.Body, []byte("JPEGDATA")) {
		t.Fatalf("body at limit must pass, got %d %q", res.Status, res.Body)
	}
}
