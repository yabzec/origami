package main

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func fakeScanner(t *testing.T) (*httptest.Server, *[]string) {
	var hits []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		hits = append(hits, r.Method+" "+r.URL.Path+" "+string(body))
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
	return srv, &hits
}

func executorFor(srv *httptest.Server) *Executor {
	sc := Scanner{UUID: "u1", Name: "HP", BaseURL: srv.URL + "/eSCL"}
	return &Executor{Client: srv.Client(), Lookup: func(uuid string) (Scanner, bool) {
		return sc, uuid == sc.UUID
	}}
}

func TestDoPostsAndRewritesLocation(t *testing.T) {
	srv, hits := fakeScanner(t)
	res := executorFor(srv).Do(context.Background(), "u1", "POST", "ScanJobs", "<xml/>")
	if res.Status != 201 {
		t.Fatalf("status %d", res.Status)
	}
	if res.Headers["Location"] != "ScanJobs/7" {
		t.Fatalf("location %q", res.Headers["Location"])
	}
	if (*hits)[0] != "POST /eSCL/ScanJobs <xml/>" {
		t.Fatalf("hit %q", (*hits)[0])
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
	srv, hits := fakeScanner(t)
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
	if len(*hits) != 0 {
		t.Fatalf("scanner was called: %v", *hits)
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
