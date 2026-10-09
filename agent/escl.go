package main

import (
	"context"
	"crypto/tls"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"runtime"
	"strings"
	"time"
)

var maxBody int64 = 200 << 20 // 200 MB: far above a 600 dpi colour page

var safePath = regexp.MustCompile(`^[A-Za-z0-9._\-/]+$`)

// Scanner is an eSCL scanner found on the local network. Its address never leaves the agent.
type Scanner struct {
	UUID    string
	Name    string
	BaseURL string // scheme://host:port/<eSCL root>, no trailing slash
}

type Result struct {
	Status      int
	ContentType string
	Headers     map[string]string
	Body        []byte
}

// Executor runs eSCL requests, but only for discovered scanners and only under their eSCL root.
type Executor struct {
	Client *http.Client
	Lookup func(uuid string) (Scanner, bool)
}

func osName() string { return runtime.GOOS + "/" + runtime.GOARCH }

// noRedirects stops the client from following a scanner redirect to another host.
func noRedirects(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }

func newHTTPClient() *http.Client {
	return &http.Client{
		Timeout:       130 * time.Second,
		CheckRedirect: noRedirects,
		Transport: &http.Transport{
			// _uscans._tcp scanners use self-signed certificates.
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true}, //nolint:gosec
		},
	}
}

func validPath(p string) bool {
	return p != "" && !strings.HasPrefix(p, "/") && !strings.Contains(p, "..") && safePath.MatchString(p)
}

func forbidden() Result { return Result{Status: http.StatusForbidden} }

func (e *Executor) Do(ctx context.Context, uuid, method, path, body string) Result {
	if method != http.MethodGet && method != http.MethodPost && method != http.MethodDelete {
		return forbidden()
	}
	sc, ok := e.Lookup(uuid)
	if !ok || !validPath(path) {
		return forbidden()
	}
	var reader io.Reader
	if body != "" {
		reader = strings.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, sc.BaseURL+"/"+path, reader)
	if err != nil {
		return Result{Status: http.StatusBadGateway}
	}
	if body != "" {
		req.Header.Set("Content-Type", "text/xml")
	}
	client := *e.Client
	client.CheckRedirect = noRedirects
	resp, err := client.Do(req)
	if err != nil {
		return Result{Status: http.StatusBadGateway}
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxBody+1))
	if err != nil {
		return Result{Status: http.StatusBadGateway}
	}
	if int64(len(data)) > maxBody {
		return Result{Status: http.StatusBadGateway}
	}
	headers := map[string]string{}
	if loc := resp.Header.Get("Location"); loc != "" {
		headers["Location"] = relativeLocation(sc.BaseURL, loc)
	}
	return Result{Status: resp.StatusCode, ContentType: resp.Header.Get("Content-Type"), Headers: headers, Body: data}
}

// relativeLocation turns a job URL from the scanner into a path relative to the eSCL root.
func relativeLocation(baseURL, loc string) string {
	u, err := url.Parse(loc)
	if err != nil {
		return ""
	}
	p := u.Path
	if b, err := url.Parse(baseURL); err == nil {
		root := strings.TrimSuffix(b.Path, "/") + "/"
		p = strings.TrimPrefix(p, root)
	}
	return strings.Trim(p, "/")
}
