package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
)

const (
	IdleTimeout        = 30 * time.Minute
	ReconnectWindow    = 60 * time.Second
	PingInterval       = 30 * time.Second
	RediscoverInterval = 60 * time.Second
	browseTimeout      = 3 * time.Second
	maxMissedBrowses   = 3 // a scanner is dropped after this many browses in a row without it
	retryDelay         = 3 * time.Second
	dialTimeout        = 15 * time.Second
	statusBadToken     = websocket.StatusCode(4401)
	statusSuperseded   = websocket.StatusCode(4000)
)

var errIdle = errors.New("idle timeout")

type Agent struct {
	mu           sync.Mutex
	server       string
	token        string
	scanners     map[string]Scanner
	missed       map[string]int // consecutive browses without the scanner
	lastActivity time.Time
	browse       Browser
	exec         *Executor
	relaunch     chan struct{}
	gen          int // incremented by Relaunch
}

func NewAgent(server, token string, browse Browser) *Agent {
	a := &Agent{server: server, token: token, scanners: map[string]Scanner{}, missed: map[string]int{},
		lastActivity: time.Now(), browse: browse, relaunch: make(chan struct{}, 1)}
	a.exec = &Executor{Client: newHTTPClient(), Lookup: a.lookup}
	return a
}

func (a *Agent) lookup(uuid string) (Scanner, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	sc, ok := a.scanners[uuid]
	return sc, ok
}

// Relaunch switches to a new server/token (from a second launch) and reconnects.
func (a *Agent) Relaunch(server, token string) {
	a.mu.Lock()
	a.server, a.token, a.lastActivity = server, token, time.Now()
	a.gen++
	a.mu.Unlock()
	select {
	case a.relaunch <- struct{}{}:
	default:
	}
}

func wsURL(server, token string) (string, error) {
	u, err := url.Parse(strings.TrimSuffix(server, "/"))
	if err != nil {
		return "", err
	}
	switch u.Scheme {
	case "https":
		u.Scheme = "wss"
	case "http":
		u.Scheme = "ws"
	}
	u.Path += "/api/agent/ws"
	u.RawQuery = url.Values{"token": {token}}.Encode()
	return u.String(), nil
}

// Run serves until idle for IdleTimeout, or until no welcomed session for ReconnectWindow.
func (a *Agent) Run(ctx context.Context) error {
	lostAt := time.Now()
	for {
		select { // drop a stale relaunch signal; the session reads the latest server/token
		case <-a.relaunch:
		default:
		}
		connCtx, cancel := context.WithCancel(ctx)
		go func() {
			select {
			case <-a.relaunch:
				cancel()
			case <-connCtx.Done():
			}
		}()
		remaining := ReconnectWindow - time.Since(lostAt)
		if remaining <= 0 {
			cancel()
			return errors.New("server unreachable")
		}
		welcomed, relaunched, err := a.session(connCtx, min(dialTimeout, remaining))
		cancel()
		if errors.Is(err, errIdle) || ctx.Err() != nil {
			return err
		}
		if st := websocket.CloseStatus(err); st == statusBadToken || st == statusSuperseded {
			return fmt.Errorf("server rejected agent: %w", err)
		}
		if welcomed || relaunched {
			lostAt = time.Now()
		}
		if time.Since(lostAt) > ReconnectWindow {
			return errors.New("server unreachable")
		}
		log.Printf("connection lost: %v; retrying", err)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(retryDelay):
		}
	}
}

func writeJSON(ctx context.Context, c *websocket.Conn, v any) error {
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return c.Write(ctx, websocket.MessageText, data)
}

// session runs one WebSocket connection. welcomed reports whether the server
// accepted us (sent a welcome); relaunched whether Relaunch ran meanwhile.
func (a *Agent) session(ctx context.Context, dialWait time.Duration) (welcomed, relaunched bool, err error) {
	a.mu.Lock()
	target, err := wsURL(a.server, a.token)
	gen := a.gen
	a.mu.Unlock()
	defer func() {
		a.mu.Lock()
		relaunched = a.gen != gen
		a.mu.Unlock()
	}()
	if err != nil {
		return false, false, err
	}
	dctx, dcancel := context.WithTimeout(ctx, dialWait)
	c, _, err := websocket.Dial(dctx, target, nil)
	dcancel()
	if err != nil {
		return false, false, err
	}
	defer c.CloseNow()
	c.SetReadLimit(1 << 20)
	ctx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)

	if err := writeJSON(ctx, c, helloMsg()); err != nil {
		return false, false, err
	}
	discoverNow := make(chan struct{}, 1)
	go a.discoverLoop(ctx, c, discoverNow)
	go a.pingLoop(ctx, c)
	go a.idleLoop(ctx, cancel)

	for {
		_, data, err := c.Read(ctx)
		if err != nil {
			if cause := context.Cause(ctx); errors.Is(cause, errIdle) {
				_ = c.Close(websocket.StatusNormalClosure, "idle")
				return welcomed, false, errIdle
			}
			return welcomed, false, err
		}
		var msg Incoming
		if json.Unmarshal(data, &msg) != nil {
			continue
		}
		switch msg.Type {
		case "welcome":
			a.mu.Lock()
			if msg.ResumeToken != "" && a.gen == gen {
				a.token = msg.ResumeToken
				welcomed = true
			}
			a.mu.Unlock()
		case "discover":
			select {
			case discoverNow <- struct{}{}:
			default: // a browse is already queued
			}
		case "escl":
			if len(msg.ID) != IDLen {
				log.Printf("ignoring escl request with bad id length %d", len(msg.ID))
				continue
			}
			a.mu.Lock()
			a.lastActivity = time.Now()
			a.mu.Unlock()
			go a.handle(ctx, c, msg)
		}
	}
}

func (a *Agent) handle(ctx context.Context, c *websocket.Conn, msg Incoming) {
	res := a.exec.Do(ctx, msg.ScannerUUID, msg.Method, msg.Path, msg.Body)
	if writeJSON(ctx, c, responseMsg(msg.ID, res)) != nil {
		return
	}
	for _, frame := range chunks(msg.ID, res.Body) {
		if c.Write(ctx, websocket.MessageBinary, frame) != nil {
			return
		}
	}
	_ = writeJSON(ctx, c, endMsg(msg.ID))
}

func (a *Agent) discoverLoop(ctx context.Context, c *websocket.Conn, now <-chan struct{}) {
	var last string
	for {
		devices := a.applyBrowse(a.browse(ctx, browseTimeout))
		key, _ := json.Marshal(devices)
		if string(key) != last {
			if writeJSON(ctx, c, devicesMsg(devices)) != nil {
				return
			}
			last = string(key)
		}
		select {
		case <-ctx.Done():
			return
		case <-now:
		case <-time.After(RediscoverInterval):
		}
	}
}

// applyBrowse merges one browse result into the known scanners. mDNS answers
// get lost now and then, so a scanner stays until maxMissedBrowses in a row miss it.
func (a *Agent) applyBrowse(found []Scanner) []Device {
	a.mu.Lock()
	defer a.mu.Unlock()
	seen := map[string]bool{}
	for _, sc := range found {
		a.scanners[sc.UUID] = sc
		delete(a.missed, sc.UUID)
		seen[sc.UUID] = true
	}
	for uuid := range a.scanners {
		if seen[uuid] {
			continue
		}
		a.missed[uuid]++
		if a.missed[uuid] >= maxMissedBrowses {
			delete(a.scanners, uuid)
			delete(a.missed, uuid)
		}
	}
	devices := make([]Device, 0, len(a.scanners))
	for _, sc := range a.scanners {
		devices = append(devices, Device{UUID: sc.UUID, Name: sc.Name})
	}
	sort.Slice(devices, func(i, j int) bool { return devices[i].UUID < devices[j].UUID })
	return devices
}

func (a *Agent) pingLoop(ctx context.Context, c *websocket.Conn) {
	t := time.NewTicker(PingInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			pctx, cancel := context.WithTimeout(ctx, 10*time.Second)
			err := c.Ping(pctx)
			cancel()
			if err != nil {
				_ = c.Close(websocket.StatusGoingAway, "ping failed")
				return
			}
		}
	}
}

func (a *Agent) idleLoop(ctx context.Context, cancel context.CancelCauseFunc) {
	t := time.NewTicker(time.Minute)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			a.mu.Lock()
			idle := time.Since(a.lastActivity) > IdleTimeout
			a.mu.Unlock()
			if idle {
				cancel(errIdle)
				return
			}
		}
	}
}
