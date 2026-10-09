package main

import (
	"context"
	"encoding/json"
	"errors"
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
	retryDelay         = 3 * time.Second
)

var errIdle = errors.New("idle timeout")

type Agent struct {
	mu           sync.Mutex
	server       string
	token        string
	scanners     map[string]Scanner
	lastActivity time.Time
	browse       Browser
	exec         *Executor
	relaunch     chan struct{}
}

func NewAgent(server, token string, browse Browser) *Agent {
	a := &Agent{server: server, token: token, scanners: map[string]Scanner{},
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

// Run serves until idle for IdleTimeout, or until no connection for ReconnectWindow.
func (a *Agent) Run(ctx context.Context) error {
	lostAt := time.Time{}
	for {
		connCtx, cancel := context.WithCancel(ctx)
		go func() {
			select {
			case <-a.relaunch:
				cancel()
			case <-connCtx.Done():
			}
		}()
		connected, err := a.session(connCtx)
		cancel()
		if errors.Is(err, errIdle) || ctx.Err() != nil {
			return err
		}
		if connected {
			lostAt = time.Now()
		} else if lostAt.IsZero() {
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

// session runs one WebSocket connection. connected reports whether the dial worked.
func (a *Agent) session(ctx context.Context) (connected bool, err error) {
	a.mu.Lock()
	target, err := wsURL(a.server, a.token)
	a.mu.Unlock()
	if err != nil {
		return false, err
	}
	c, _, err := websocket.Dial(ctx, target, nil)
	if err != nil {
		return false, err
	}
	defer c.CloseNow()
	c.SetReadLimit(1 << 20)
	ctx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)

	if err := writeJSON(ctx, c, helloMsg()); err != nil {
		return true, err
	}
	go a.discoverLoop(ctx, c)
	go a.pingLoop(ctx, c)
	go a.idleLoop(ctx, cancel)

	for {
		_, data, err := c.Read(ctx)
		if err != nil {
			if cause := context.Cause(ctx); errors.Is(cause, errIdle) {
				_ = c.Close(websocket.StatusNormalClosure, "idle")
				return true, errIdle
			}
			return true, err
		}
		var msg Incoming
		if json.Unmarshal(data, &msg) != nil {
			continue
		}
		switch msg.Type {
		case "welcome":
			a.mu.Lock()
			a.token = msg.ResumeToken
			a.mu.Unlock()
		case "escl":
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

func (a *Agent) discoverLoop(ctx context.Context, c *websocket.Conn) {
	var last string
	for {
		found := a.browse(ctx, browseTimeout)
		devices := make([]Device, 0, len(found))
		next := map[string]Scanner{}
		for _, sc := range found {
			next[sc.UUID] = sc
			devices = append(devices, Device{UUID: sc.UUID, Name: sc.Name})
		}
		sort.Slice(devices, func(i, j int) bool { return devices[i].UUID < devices[j].UUID })
		a.mu.Lock()
		a.scanners = next
		a.mu.Unlock()
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
		case <-time.After(RediscoverInterval):
		}
	}
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
