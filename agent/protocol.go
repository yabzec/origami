package main

// Version must match AGENT_VERSION in backend/app/services/agent_hub.py.
const Version = "0.1.0"

// ChunkSize is the largest body slice sent in one binary frame.
const ChunkSize = 262144

// IDLen is the length of the ASCII request id that prefixes every binary frame.
const IDLen = 16

// Incoming is any JSON message the server sends.
type Incoming struct {
	Type        string `json:"type"`
	ID          string `json:"id"`
	ScannerUUID string `json:"scanner_uuid"`
	Method      string `json:"method"`
	Path        string `json:"path"`
	Body        string `json:"body"`
	ResumeToken string `json:"resume_token"`
}

type Device struct {
	UUID string `json:"uuid"`
	Name string `json:"name"`
}

func helloMsg() map[string]any {
	return map[string]any{"type": "hello", "version": Version, "os": osName()}
}

func devicesMsg(d []Device) map[string]any {
	if d == nil {
		d = []Device{}
	}
	return map[string]any{"type": "devices", "devices": d}
}

func responseMsg(id string, r Result) map[string]any {
	headers := r.Headers
	if headers == nil {
		headers = map[string]string{}
	}
	return map[string]any{
		"type": "escl_response", "id": id, "status": r.Status,
		"content_type": r.ContentType, "headers": headers, "length": len(r.Body),
	}
}

func endMsg(id string) map[string]any {
	return map[string]any{"type": "end", "id": id}
}

func chunks(id string, body []byte) [][]byte {
	var out [][]byte
	for start := 0; start < len(body); start += ChunkSize {
		end := min(start+ChunkSize, len(body))
		frame := make([]byte, 0, IDLen+end-start)
		frame = append(frame, id...)
		frame = append(frame, body[start:end]...)
		out = append(out, frame)
	}
	return out
}
