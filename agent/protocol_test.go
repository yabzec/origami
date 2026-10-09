package main

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestChunksSplitAndPrefix(t *testing.T) {
	id := "0123456789abcdef"
	body := bytes.Repeat([]byte("x"), ChunkSize*2+10)
	got := chunks(id, body)
	if len(got) != 3 {
		t.Fatalf("want 3 chunks, got %d", len(got))
	}
	var joined []byte
	for _, c := range got {
		if string(c[:IDLen]) != id {
			t.Fatalf("chunk without id prefix")
		}
		if len(c)-IDLen > ChunkSize {
			t.Fatalf("chunk too big: %d", len(c)-IDLen)
		}
		joined = append(joined, c[IDLen:]...)
	}
	if !bytes.Equal(joined, body) {
		t.Fatal("chunks do not rebuild the body")
	}
	if len(chunks(id, nil)) != 0 {
		t.Fatal("empty body must give no chunks")
	}
}

func TestDevicesMsgSendsEmptyList(t *testing.T) {
	raw, _ := json.Marshal(devicesMsg(nil))
	if string(raw) != `{"devices":[],"type":"devices"}` {
		t.Fatalf("got %s", raw)
	}
}

func TestResponseMsg(t *testing.T) {
	raw, _ := json.Marshal(responseMsg("id1", Result{Status: 201, ContentType: "text/xml",
		Headers: map[string]string{"Location": "ScanJobs/1"}, Body: []byte("abc")}))
	var m map[string]any
	_ = json.Unmarshal(raw, &m)
	if m["type"] != "escl_response" || m["status"].(float64) != 201 || m["length"].(float64) != 3 {
		t.Fatalf("bad message %s", raw)
	}
}
