# openagent — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## tool/local_file.go

Source: https://github.com/the-open-agent/openagent/blob/f2338321f65ad7dd65d0749ad0bd95efa7c2c881/tool/local_file.go

SHA-256: `d52186d045b949685b8719fc86203ae6ee09dd76754f97351cc3ff609260aac2`

```
// Copyright 2026 The OpenAgent Authors. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package tool

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"mime/multipart"
	"net/http"
	"os"
	"os/user"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/ThinkInAIXYZ/go-mcp/protocol"
	"github.com/the-open-agent/openagent/internal/localocr"
	"github.com/the-open-agent/openagent/txt"
)

const (
	localFileDefaultPreviewChars = 1200
	localFileMaxPreviewChars     = 20000
	localFileDefaultReadLimit    = 12000
	localFileMaxReadLimit        = 100000
	localFileOcrTimeout          = 2 * time.Minute
)

// LocalFileTool is the Tool Type "local_file".
type LocalFileTool struct {
	lang        string
	ocrEndpoint string
}

func (p *LocalFileTool) BuiltinTools() []BuiltinTool {
	return []BuiltinTool{
		&localSpecialDirsBuiltin{},
		&localFileScanBuiltin{lang: p.lang},
		&localFileReadBuiltin{lang: p.lang},
		&localPdfOcrReadBuiltin{endpoint: p.ocrEndpoint},
		&localFileWriteBuiltin{},
		&localFileMoveBuiltin{},
	}
}

type localFileScanItem struct {
	Type         string `json:"type"`
	Name         string `json:"name"`
	Path         string `json:"path"`
	Size         int64  `json:"size,omitempty"`
	ModifiedTime string `json:"modifiedTime,omitempty"`
	TextLength   int    `json:"textLength,omitempty"`
	Preview      string `json:"preview,omitempty"`
	Truncated    bool   `json:"truncated,omitempty"`
	Error        string `json:"error,omitempty"`
}

type localSpecialDirInfo struct {
	Path   string `json:"path"`
	Exists bool   `json:"exists"`
}

type localSpecialDirs struct {
	Desktop   localSpecialDirInfo `json:"desktop"`
	Documents localSpecialDirInfo `json:"documents"`
	Downloads localSpecialDirInfo `json:"downloads"`
}

type localSpecialDirsResult struct {
	OS          string           `json:"os"`
	Username    string           `json:"username"`
	Home        string           `json:"home"`
	Directories localSpecialDirs `json:"directories"`
}

type localFileScanResult struct {
	Root  string              `json:"root"`
	Count int                 `json:"count"`
	Items []localFileScanItem `json:"items"`
}

type localFileReadResult struct {
	Path       string `json:"path"`
	Extension  string `json:"extension"`
	Offset     int    `json:"offset"`
	Limit      int    `json:"limit"`
	TextLength int    `json:"textLength"`
	Text       string `json:"text"`
	Truncated  bool   `json:"truncated"`
}

type localPdfOcrReadResult struct {
	Path       string `json:"path"`
	TextLength int    `json:"textLength"`
	Text       string `json:"text"`
}

type localPdfOcrResponse struct {
	Text *string `json:"text"`
}

type localFileWriteResult struct {
	Path      string `json:"path"`
	ByteSize  int    `json:"byteSize"`
	Overwrote bool   `json:"overwrote"`
}

type localFileMoveResult struct {
	Source    string `json:"source"`
	Target    string `json:"target"`
	Overwrote bool   `json:"overwrote"`
}

func localFileText(text string) *protocol.CallToolResult {
	return &protocol.CallToolResult{
		IsError: false,
		Content: []protocol.Content{
			&protocol.TextContent{Type: "text", Text: text},
		},
	}
}

func localFileError(text string) *protocol.CallToolResult {
	return &protocol.CallToolResult{
		IsError: true,
		Content: []protocol.Content{
			&protocol.TextContent{Type: "text", Text: text},
		},
	}
}

func localFileJSON(v interface{}) *protocol.CallToolResult {
	bs, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return localFileError(err.Error())
	}
	return localFileText(string(bs))
}

var (
	localFileRename = os.Rename
	localFileRemove = os.Remove
)

func localFileStringArg(arguments map[string]interface{}, key string) string {
	value, _ := arguments[key].(string)
	return strings.TrimSpace(value)
}

func localFileBoolArg(arguments map[string]interface{}, key string) bool {
	value, _ := arguments[key].(bool)
	return value
}

func localFileIntArg(arguments map[string]interface{}, key string, defaultValue int) int {
	value, ok := arguments[key]
	if !ok {
		return defaultValue
	}
	switch v := value.(type) {
	case int:
		return v
	case int64:
		return int(v)
	case float64:
		return int(v)
	case json.Number:
		n, err := v.Int64()
		if err == nil {
			return int(n)
		}
	}
	return defaultValue
}

func localFileRequireAbsolutePath(path, field string) error {
	if strings.TrimSpace(path) == "" {
		return fmt.Errorf("missing required parameter: %s", field)
	}
	if !filepath.IsAbs(path) {
		return fmt.Errorf("%s must be an absolute path for the current operating system: %s", field, path)
	}
	return nil
}

func localFileSupportedExt(path string) (string, bool) {
	ext := strings.ToLower(filepath.Ext(path))
	for _, supportedExt := range txt.GetSupportedFileTypes() {
		if ext == supportedExt {
			return ext, true
		}
	}
	return ext, false
}

func localFileClamp(value, minValue, maxValue int) int {
	if value < minValue {
		return minValue
	}
	if value > maxValue {
		return maxValue
	}
	return value
}

func localFileSliceText(text string, offset, limit int) (string, bool) {
	runes := []rune(text)
	if offset > len(runes) {
		return "", false
	}
	end := offset + limit
	truncated := false
	if end < len(runes) {
		truncated = true
	} else {
		end = len(runes)
	}
	return string(runes[offset:end]), truncated
}

func localFileReadText(path, ext, lang string) (string, error) {
	if _, ok := localFileSupportedExt(path); ok {
		return txt.GetParsedTextFromUrl(path, ext, lang)
	}
	bs, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	if !utf8.Valid(bs) {
		return "", fmt.Errorf("file content is not valid UTF-8 text")
	}
	return string(bs), nil
}

func localFileOcrEndpoint(ctx context.Context, endpoint string) (string, error) {
	endpoint = strings.TrimSpace(endpoint)
	if endpoint != "" {
		return endpoint, nil
	}
	return localocr.EnsureRunning(ctx)
}

func localFilePostPdfOcr(ctx context.Context, endpoint string, path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()

	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("file", filepath.Base(path))
	if err != nil {
		return "", err
	}
	if _, err = io.Copy(part, file); err != nil {
		return "", err
	}
	if err = writer.Close(); err != nil {
		return "", err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, &body)
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())

	client := &http.Client{Timeout: localFileOcrTimeout}
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		return "", fmt.Errorf("ocr endpoint returned status %d", resp.StatusCode)
	}

	var result localPdfOcrResponse
	if err = json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", err
	}
	if result.Text == nil {
		return "", fmt.Errorf("ocr endpoint response missing text")
	}
	return *result.Text, nil
}

func localFileDirectory(path string) localSpecialDirInfo {
	info, err := os.Stat(path)
	return localSpecialDirInfo{
		Path:   path,
		Exists: err == nil && info.IsDir(),
	}
}

func localFileDesktopPath(home string) string {
	desktop := filepath.Join(home, "Desktop")
	if runtime.GOOS == "windows" {
		oneDriveDesktop := filepath.Join(home, "OneDrive", "Desktop")
		if localFileDirectory(oneDriveDesktop).Exists {
			return oneDriveDesktop
		}
	}
	return desktop
}

type localSpecialDirsBuiltin struct{}

func (b *localSpecialDirsBuiltin) GetName() string {
	return "local_special_dirs"
}

func (b *localSpecialDirsBuiltin) GetDescription() string {
	return `Return common local directories for the operating system user running the OpenAgent backend process.
- No required parameters.
- Returns os, username, home, and Desktop/Documents/Downloads paths with existence flags.
- Use this before scanning when the user says "my Desktop", "my Documents", or "Downloads" without an absolute path.`
}

func (b *localSpecialDirsBuiltin) GetInputSchema() interface{} {
	return map[string]interface{}{
		"type":       "object",
		"properties": map[string]interface{}{},
	}
}

func (b *localSpecialDirsBuiltin) Execute(_ context.Context, _ map[string]interface{}) (*protocol.CallToolResult, error) {
	currentUser, err := user.Current()
	if err != nil {
		return localFileError(fmt.Sprintf("failed to read current process user: %s", err.Error())), nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return localFileError(fmt.Sprintf("failed to read current process user home: %s", err.Error())), nil
	}
	if !filepath.IsAbs(home) {
		return localFileError(fmt.Sprintf("current process user home is not absolute: %s", home)), nil
	}

	return localFileJSON(localSpecialDirsResult{
		OS:       runtime.GOOS,
		Username: currentUser.Username,
		Home:     home,
		Directories: localSpecialDirs{
			Desktop:   localFileDirectory(localFileDesktopPath(home)),
			Documents: localFileDirectory(filepath.Join(home, "Documents")),
			Downloads: localFileDirectory(filepath.Join(home, "Downloads")),
		},
	}), nil
}

type localPdfOcrReadBuiltin struct {
	endpoint string
}

func (b *localPdfOcrReadBuiltin) GetName() string {
	return "local_pdf_ocr_read"
}

func (b *localPdfOcrReadBuiltin) GetDescription() string {
	return `Read a scanned local PDF through the configured OCR HTTP endpoint.
- path (required): absolute PDF file path for the current operating system.
- Sends multipart/form-data field "file" to the local_file Provider URL, or the managed local OCR service when Provider URL is empty.
- The OCR endpoint must return JSON: {"text":"recognized text"}.`
}

func (b *localPdfOcrReadBuiltin) GetInputSchema() interface{} {
	return map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"path": map[string]interface{}{
				"type":        "string",
				"description": "Absolute path to a local PDF file.",
			},
		},
		"required": []string{"path"},
	}
}

func (b *localPdfOcrReadBuiltin) Execute(ctx context.Context, arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	path := localFileStringArg(arguments, "path")
	if err := localFileRequireAbsolutePath(path, "path"); err != nil {
		return localFileError(err.Error()), nil
	}
	info, err := os.Stat(path)
	if err != nil {
		return localFileError(fmt.Sprintf("failed to access path: %s", err.Error())), nil
	}
	if info.IsDir() {
		return localFileError("path must be a file"), nil
	}
	if strings.ToLower(filepath.Ext(path)) != ".pdf" {
		return localFileError("path must be a PDF file"), nil
	}

	endpoint, err := localFileOcrEndpoint(ctx, b.endpoint)
	if err != nil {
		return localFileError(fmt.Sprintf("failed to start managed OCR service: %s", err.Error())), nil
	}

	text, err := localFilePostPdfOcr(ctx, endpoint, path)
	if err != nil {
		return localFileError(fmt.Sprintf("failed to read PDF through OCR endpoint: %s", err.Error())), nil
	}
	return localFileJSON(localPdfOcrReadResult{
		Path:       path,
		TextLength: len([]rune(text)),
		Text:       text,
	}), nil
}

type localFileScanBuiltin struct {
	lang string
}

func (b *localFileScanBuiltin) GetName() string {
	return "local_file_scan"
}

func (b *localFileScanBuiltin) GetDescription() string {
	return `Scan a local directory recursively and return a JSON manifest of all descendant files and directories.
- root (required): absolute directory path for the current operating system.
- preview_chars: maximum text preview characters per file (default 1200, max 20000). Set to 0 to skip previews.
- Returns items with type, name, path, size, modifiedTime, and a text preview for readable files.`
}

func (b *localFileScanBuiltin) GetInputSchema() interface{} {
	return map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"root": map[string]interface{}{
				"type":        "string",
				"description": "Absolute directory path to scan.",
			},
			"preview_chars": map[string]interface{}{
				"type":        "number",
				"description": "Maximum text preview characters per file (default 1200, max 20000). Set to 0 to skip previews.",
			},
		},
		"required": []string{"root"},
	}
}

func (b *localFileScanBuiltin) Execute(_ context.Context, arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	root := localFileStringArg(arguments, "root")
	if err := localFileRequireAbsolutePath(root, "root"); err != nil {
		return localFileError(err.Error()), nil
	}

	info, err := os.Stat(root)
	if err != nil {
		return localFileError(fmt.Sprintf("failed to access root: %s", err.Error())), nil
	}
	if !info.IsDir() {
		return localFileError("root must be a directory"), nil
	}

	previewChars := localFileIntArg(arguments, "preview_chars", localFileDefaultPreviewChars)
	previewChars = localFileClamp(previewChars, 0, localFileMaxPreviewChars)

	var items []localFileScanItem
	walkErr := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if path == root {
			return nil
		}

		if d.IsDir() {
			items = append(items, localFileScanItem{
				Type: "directory",
				Name: d.Name(),
				Path: path,
			})
			return nil
		}

		item := localFileScanItem{
			Type: "file",
			Name: d.Name(),
			Path: path,
		}
		if fileInfo, statErr := d.Info(); statErr == nil {
			item.Size = fileInfo.Size()
			item.ModifiedTime = fileInfo.ModTime().Format("2006-01-02 15:04:05")
		}
		if previewChars > 0 {
			ext := strings.ToLower(filepath.Ext(path))
			if text, readErr := localFileReadText(path, ext, b.lang); readErr != nil {
				item.Error = readErr.Error()
			} else {
				item.TextLength = len([]rune(text))
				item.Preview, item.Truncated = localFileSliceText(text, 0, previewChars)
			}
		}
		items = append(items, item)
		return nil
	})
	if walkErr != nil {
		return localFileError(fmt.Sprintf("failed to scan root: %s", walkErr.Error())), nil
	}

	sort.Slice(items, func(i, j int) bool {
		return items[i].Path < items[j].Path
	})

	return localFileJSON(localFileScanResult{
		Root:  root,
		Count: len(items),
		Items: items,
	}), nil
}

type localFileReadBuiltin struct {
	lang string
}

func (b *localFileReadBuiltin) GetName() string {
	return "local_file_read"
}

func (b *localFileReadBuiltin) GetDescription() string {
	return `Read text from a local file.
- path (required): absolute file path for the current operating system.
- For supported document types, extracts text with the document parser.
- For other files, reads UTF-8 text directly.
- offset: character offset in returned text (default 0).
- limit: maximum characters to return (default 12000, max 100000).`
}

func (b *localFileReadBuiltin) GetInputSchema() interface{} {
	return map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"path": map[string]interface{}{
				"type":        "string",
				"description": "Absolute path to a file.",
			},
			"offset": map[string]interface{}{
				"type":        "number",
				"description": "Character offset in returned text (default 0).",
			},
			"limit": map[string]interface{}{
				"type":        "number",
				"description": "Maximum characters to return (default 12000, max 100000).",
			},
		},
		"required": []string{"path"},
	}
}

func (b *localFileReadBuiltin) Execute(_ context.Context, arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	path := localFileStringArg(arguments, "path")
	if err := localFileRequireAbsolutePath(path, "path"); err != nil {
		return localFileError(err.Error()), nil
	}
	info, err := os.Stat(path)
	if err != nil {
		return localFileError(fmt.Sprintf("failed to access path: %s", err.Error())), nil
	}
	if info.IsDir() {
		return localFileError("path must be a file"), nil
	}
	ext := strings.ToLower(filepath.Ext(path))

	offset := localFileIntArg(arguments, "offset", 0)
	if offset < 0 {
		offset = 0
	}
	limit := localFileIntArg(arguments, "limit", localFileDefaultReadLimit)
	limit = localFileClamp(limit, 0, localFileMaxReadLimit)

	text, err := localFileReadText(path, ext, b.lang)
	if err != nil {
		return localFileError(fmt.Sprintf("failed to read file: %s", err.Error())), nil
	}
	section, truncated := localFileSliceText(text, offset, limit)

	return localFileJSON(localFileReadResult{
		Path:       path,
		Extension:  ext,
		Offset:     offset,
		Limit:      limit,
		TextLength: len([]rune(text)),
		Text:       section,
		Truncated:  truncated,
	}), nil
}

type localFileWriteBuiltin struct{}

func (b *localFileWriteBuiltin) GetName() string {
	return "local_file_write"
}

func (b *localFileWriteBuiltin) GetDescription() string {
	return `Write text content to a local file.
- path (required): absolute output file path for the current operating system.
- content (required): text content to write.
- overwrite: set true to replace an existing file (default false).`
}

func (b *localFileWriteBuiltin) GetInputSchema() interface{} {
	return map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"path": map[string]interface{}{
				"type":        "string",
				"description": "Absolute output file path.",
			},
			"content": map[string]interface{}{
				"type":        "string",
				"description": "Text content to write.",
			},
			"overwrite": map[string]interface{}{
				"type":        "boolean",
				"description": "Replace an existing file when true (default false).",
			},
		},
		"required": []string{"path", "content"},
	}
}

func (b *localFileWriteBuiltin) Execute(_ context.Context, arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	path := localFileStringArg(arguments, "path")
	if err := localFileRequireAbsolutePath(path, "path"); err != nil {
		return localFileError(err.Error()), nil
	}
	content, ok := arguments["content"].(string)
	if !ok {
		return localFileError("missing required parameter: content"), nil
	}
	overwrite := localFileBoolArg(arguments, "overwrite")

	if info, err := os.Stat(path); err == nil && info.IsDir() {
		return localFileError("path must be a file"), nil
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return localFileError(fmt.Sprintf("failed to create parent directory: %s", err.Error())), nil
	}

	overwrote := false
	if overwrite {
		if _, err := os.Stat(path); err == nil {
			overwrote = true
		}
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			return localFileError(fmt.Sprintf("failed to write file: %s", err.Error())), nil
		}
	} else {
		file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if err != nil {
			if os.IsExist(err) {
				return localFileError("target file already exists; set overwrite to true to replace it"), nil
			}
			return localFileError(fmt.Sprintf("failed to create file: %s", err.Error())), nil
		}
		if _, err := file.WriteString(content); err != nil {
			_ = file.Close()
			return localFileError(fmt.Sprintf("failed to write file: %s", err.Error())), nil
		}
		if err := file.Close(); err != nil {
			return localFileError(fmt.Sprintf("failed to close file: %s", err.Error())), nil
		}
	}

	return localFileJSON(localFileWriteResult{
		Path:      path,
		ByteSize:  len([]byte(content)),
		Overwrote: overwrote,
	}), nil
}

type localFileMoveBuiltin struct{}

func (b *localFileMoveBuiltin) GetName() string {
	return "local_file_move"
}

func (b *localFileMoveBuiltin) GetDescription() string {
	return `Move one local file after explicit user confirmation.
- source (required): absolute source file path for the current operating system.
- target (required): absolute target file path for the current operating system.
- confirmed (required): must be true; call only after the user explicitly confirms the move plan.
- overwrite: set true to replace an existing target file (default false).`
}

func (b *localFileMoveBuiltin) GetInputSchema() interface{} {
	return map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"source": map[string]interface{}{
				"type":        "string",
				"description": "Absolute source file path.",
			},
			"target": map[string]interface{}{
				"type":        "string",
				"description": "Absolute target file path.",
			},
			"confirmed": map[string]interface{}{
				"type":        "boolean",
				"description": "Must be true after explicit user confirmation.",
			},
			"overwrite": map[string]interface{}{
				"type":        "boolean",
				"description": "Replace an existing target file when true (default false).",
			},
		},
		"required": []string{"source", "target", "confirmed"},
	}
}

func (b *localFileMoveBuiltin) Execute(_ context.Context, arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	if !localFileBoolArg(arguments, "confirmed") {
		return localFileError("confirmed must be true before moving files"), nil
	}
	source := localFileStringArg(arguments, "source")
	target := localFileStringArg(arguments, "target")
	if err := localFileRequireAbsolutePath(source, "source"); err != nil {
		return localFileError(err.Error()), nil
	}
	if err := localFileRequireAbsolutePath(target, "target"); err != nil {
		return localFileError(err.Error()), nil
	}
	if filepath.Clean(source) == filepath.Clean(target) {
		return localFileError("source and target must be different files"), nil
	}

	sourceInfo, err := os.Stat(source)
	if err != nil {
		return localFileError(fmt.Sprintf("failed to access source: %s", err.Error())), nil
	}
	if sourceInfo.IsDir() {
		return localFileError("source must be a file"), nil
	}
	if info, err := os.Stat(target); err == nil && info.IsDir() {
		return localFileError("target must be a file"), nil
	}

	overwrite := localFileBoolArg(arguments, "overwrite")
	targetExists := false
	if _, err := os.Stat(target); err == nil {
		targetExists = true
		if !overwrite {
			return localFileError("target file already exists; set overwrite to true to replace it"), nil
		}
	} else if !os.IsNotExist(err) {
		return localFileError(fmt.Sprintf("failed to access target: %s", err.Error())), nil
	}

	dir := filepath.Dir(target)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return localFileError(fmt.Sprintf("failed to create target directory: %s", err.Error())), nil
	}
	backupPath := ""
	if targetExists {
		backupPath, err = localFileMoveBackupPath(target)
		if err != nil {
			return localFileError(fmt.Sprintf("failed to reserve target backup path: %s", err.Error())), nil
		}
		if err := localFileRename(target, backupPath); err != nil {
			return localFileError(fmt.Sprintf("failed to backup existing target: %s", err.Error())), nil
		}
	}
	if err := localFileRename(source, target); err != nil {
		if backupPath != "" {
			if restoreErr := localFileRename(backupPath, target); restoreErr != nil {
				return localFileError(fmt.Sprintf("failed to move file: %s; failed to restore existing target: %s", err.Error(), restoreErr.Error())), nil
			}
		}
		return localFileError(fmt.Sprintf("failed to move file: %s", err.Error())), nil
	}
	if backupPath != "" {
		if err := localFileRemove(backupPath); err != nil {
			return localFileError(fmt.Sprintf("failed to remove target backup after move: %s", err.Error())), nil
		}
	}

	return localFileJSON(localFileMoveResult{
		Source:    source,
		Target:    target,
		Overwrote: targetExists,
	}), nil
}

func localFileMoveBackupPath(target string) (string, error) {
	dir := filepath.Dir(target)
	base := filepath.Base(target)
	for i := 0; i < 100; i++ {
		path := filepath.Join(dir, fmt.Sprintf(".%s.openagent-move-backup-%d-%d", base, time.Now().UnixNano(), i))
		if _, err := os.Lstat(path); os.IsNotExist(err) {
			return path, nil
		} else if err != nil {
			return "", err
		}
	}
	return "", fmt.Errorf("failed to generate unique backup path for target: %s", target)
}

```

## tool/shell.go

Source: https://github.com/the-open-agent/openagent/blob/f2338321f65ad7dd65d0749ad0bd95efa7c2c881/tool/shell.go

SHA-256: `148f71aec3e40dc0b334da9efaadef9de0e0d6912adf7d99f46ba1d136c1670e`

```
// Copyright 2026 The OpenAgent Authors. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package tool

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/ThinkInAIXYZ/go-mcp/protocol"
	gopty "github.com/aymanbagabas/go-pty"
)

const (
	shellDefaultTimeoutSeconds = 30.0
	shellMaxTimeoutSeconds     = 300.0
	shellDefaultPollBytes      = 12000
	shellMaxPollBytes          = 100000
	shellDefaultPTYRows        = 40
	shellDefaultPTYCols        = 120
	shellSessionIdleTimeout    = 30 * time.Minute
)

// ShellTool is the Tool Type "shell".
type ShellTool struct{}

func (p *ShellTool) BuiltinTools() []BuiltinTool {
	return []BuiltinTool{&shellBuiltin{}}
}

type shellBuiltin struct{}

type shellSession struct {
	id           string
	command      string
	workdir      string
	usePTY       bool
	cmd          *exec.Cmd
	ptyCmd       *gopty.Cmd
	stdin        io.WriteCloser
	pty          gopty.Pty
	output       *shellRingBuffer
	done         chan struct{}
	cancel       context.CancelFunc
	startedAt    time.Time
	lastActivity time.Time
	exitErr      error
	exitCode     *int

	mu     sync.Mutex
	closed bool
}

type shellRingBuffer struct {
	mu        sync.Mutex
	buf       []byte
	readPos   int
	maxBytes  int
	truncated bool
}

type shellSessionManager struct {
	mu          sync.Mutex
	sessions    map[string]*shellSession
	nextID      uint64
	cleanupOnce sync.Once
}

var globalShellSessionManager = &shellSessionManager{
	sessions: map[string]*shellSession{},
}

func (s *shellBuiltin) GetName() string {
	return "shell"
}

func (s *shellBuiltin) GetDescription() string {
	return `Execute a shell command or manage a running shell process session.
- command (required): the shell command to run (e.g. "ls -la", "echo hello").
- timeout: execution timeout in seconds (default 30, max 300).
- workdir: working directory for the command (default: current directory).
- background: when true, start a long-running shell session and return a session_id immediately.
- pty: when true, run the command in a pseudo-terminal for interactive CLI programs.
- action: session lifecycle action. One of "start", "poll", "write", "submit", "send_keys", "resize", or "stop".
- session_id: required for process actions after start.
- input: text to write to the running terminal for "write" or "submit".
- keys: key sequence names for "send_keys", for example ["enter"], ["ctrl+c"], ["tab"].
- rows: terminal row count for PTY resize or PTY start.
- cols: terminal column count for PTY resize or PTY start.
- bytes: max bytes to return for "poll" (default 12000, max 100000).`
}

func (s *shellBuiltin) GetInputSchema() interface{} {
	return map[string]interface{}{
		"type": "object",
		"properties": map[string]interface{}{
			"command": map[string]interface{}{
				"type":        "string",
				"description": "The shell command to execute.",
			},
			"timeout": map[string]interface{}{
				"type":        "number",
				"description": "Execution timeout in seconds (default 30, max 300).",
			},
			"workdir": map[string]interface{}{
				"type":        "string",
				"description": "Working directory for the command (default: current directory).",
			},
			"background": map[string]interface{}{
				"type":        "boolean",
				"description": "When true, start a background shell session and return immediately.",
			},
			"pty": map[string]interface{}{
				"type":        "boolean",
				"description": "When true, allocate a pseudo-terminal for interactive CLI programs.",
			},
			"action": map[string]interface{}{
				"type":        "string",
				"enum":        []string{"start", "poll", "write", "submit", "send_keys", "resize", "stop"},
				"description": "Action for background shell process sessions. Defaults to start.",
			},
			"session_id": map[string]interface{}{
				"type":        "string",
				"description": "Session ID for background shell control actions.",
			},
			"input": map[string]interface{}{
				"type":        "string",
				"description": "Text to write to a background shell session.",
			},
			"bytes": map[string]interface{}{
				"type":        "number",
				"description": "Max bytes to return for poll (default 12000, max 100000).",
			},
			"keys": map[string]interface{}{
				"type":        "array",
				"description": "Key sequence names for send_keys, e.g. [\"enter\"], [\"ctrl+c\"].",
				"items": map[string]interface{}{
					"type": "string",
				},
			},
			"rows": map[string]interface{}{
				"type":        "number",
				"description": "PTY rows for start or resize.",
			},
			"cols": map[string]interface{}{
				"type":        "number",
				"description": "PTY columns for start or resize.",
			},
		},
	}
}

func (s *shellBuiltin) Execute(ctx context.Context, arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	if shellBoolArg(arguments, "background") || shellHasAction(arguments) {
		return shellExecuteBackground(ctx, arguments)
	}

	command, ok := arguments["command"].(string)
	if !ok || strings.TrimSpace(command) == "" {
		return shellError("Missing required parameter: command"), nil
	}

	timeoutSecs := shellDefaultTimeoutSeconds
	if t, ok := arguments["timeout"].(float64); ok && t > 0 {
		timeoutSecs = t
		if timeoutSecs > shellMaxTimeoutSeconds {
			timeoutSecs = shellMaxTimeoutSeconds
		}
	}

	workdir := shellStringArg(arguments, "workdir", "")
	usePTY := shellBoolArg(arguments, "pty")

	execCtx, cancel := context.WithTimeout(ctx, time.Duration(timeoutSecs)*time.Second)
	defer cancel()

	result, runErr := shellRunForeground(execCtx, command, workdir, usePTY)
	if execCtx.Err() == context.DeadlineExceeded {
		return shellError(fmt.Sprintf("Error: command timed out after %.0f seconds", timeoutSecs)), nil
	}
	if runErr != nil {
		return shellError(result), nil
	}
	return shellText(result), nil
}

func shellExecuteBackground(ctx context.Context, arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	action := shellStringArg(arguments, "action", "start")
	switch action {
	case "start":
		return shellStartBackground(ctx, arguments)
	case "poll":
		return shellPollBackground(arguments)
	case "write":
		return shellWriteBackground(arguments)
	case "submit":
		return shellSubmitBackground(arguments)
	case "send_keys":
		return shellSendKeysBackground(arguments)
	case "resize":
		return shellResizeBackground(arguments)
	case "stop":
		return shellStopBackground(arguments)
	default:
		return shellError(fmt.Sprintf("invalid action: %s", action)), nil
	}
}

func shellStartBackground(ctx context.Context, arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	command := shellStringArg(arguments, "command", "")
	if command == "" {
		return shellError("Missing required parameter: command"), nil
	}

	workdir := shellStringArg(arguments, "workdir", "")
	usePTY := shellBoolArg(arguments, "pty")

	sessionCtx := ctx
	cancel := func() {}
	if t, ok := arguments["timeout"].(float64); ok && t > 0 {
		timeoutSecs := t
		if timeoutSecs > shellMaxTimeoutSeconds {
			timeoutSecs = shellMaxTimeoutSeconds
		}
		sessionCtx, cancel = context.WithTimeout(ctx, time.Duration(timeoutSecs)*time.Second)
	} else {
		sessionCtx, cancel = context.WithCancel(ctx)
	}
	cmd, err := shellBuildCommand(sessionCtx, command)
	if err != nil {
		cancel()
		return shellError(err.Error()), nil
	}

	session := &shellSession{
		id:        globalShellSessionManager.nextSessionID(),
		command:   command,
		workdir:   workdir,
		usePTY:    usePTY,
		cmd:       cmd,
		output:    newShellRingBuffer(shellMaxPollBytes * 2),
		done:      make(chan struct{}),
		cancel:    cancel,
		startedAt: time.Now(),
	}

	if usePTY {
		if err = shellStartPTYBackground(sessionCtx, session, arguments); err != nil {
			cancel()
			return shellError(err.Error()), nil
		}
	} else {
		if workdir != "" {
			cmd.Dir = workdir
		}
		stdin, pipeErr := cmd.StdinPipe()
		if pipeErr != nil {
			cancel()
			return shellError(pipeErr.Error()), nil
		}
		stdoutPipe, pipeErr := cmd.StdoutPipe()
		if pipeErr != nil {
			cancel()
			_ = stdin.Close()
			return shellError(pipeErr.Error()), nil
		}
		stderrPipe, pipeErr := cmd.StderrPipe()
		if pipeErr != nil {
			cancel()
			_ = stdin.Close()
			_ = stdoutPipe.Close()
			return shellError(pipeErr.Error()), nil
		}

		if pipeErr = cmd.Start(); pipeErr != nil {
			cancel()
			_ = stdin.Close()
			_ = stdoutPipe.Close()
			_ = stderrPipe.Close()
			return shellError(pipeErr.Error()), nil
		}

		session.stdin = stdin
		go session.capture(stdoutPipe)
		go session.capture(stderrPipe)
	}

	go session.wait()

	globalShellSessionManager.put(session)

	return shellJSON(map[string]interface{}{
		"session_id": session.id,
		"running":    true,
		"pid":        session.processID(),
		"command":    session.command,
		"workdir":    session.workdir,
		"pty":        session.usePTY,
		"started_at": session.startedAt.Format(time.RFC3339),
		"message":    "Background shell session started. Use action=poll to read output, action=write to send input, and action=stop to terminate it.",
	}), nil
}

func shellPollBackground(arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	sessionID := shellStringArg(arguments, "session_id", "")
	if sessionID == "" {
		return shellError("Missing required parameter: session_id"), nil
	}

	session := globalShellSessionManager.get(sessionID)
	if session == nil {
		return shellError(fmt.Sprintf("shell session not found: %s", sessionID)), nil
	}

	maxBytes := shellIntArg(arguments, "bytes", shellDefaultPollBytes)
	if maxBytes <= 0 {
		maxBytes = shellDefaultPollBytes
	}
	if maxBytes > shellMaxPollBytes {
		maxBytes = shellMaxPollBytes
	}

	output, truncated := session.output.readUnread(maxBytes)
	running := session.isRunning()
	payload := map[string]interface{}{
		"session_id":       session.id,
		"running":          running,
		"pid":              session.processID(),
		"command":          session.command,
		"workdir":          session.workdir,
		"pty":              session.usePTY,
		"output":           output,
		"output_truncated": truncated,
		"started_at":       session.startedAt.Format(time.RFC3339),
		"exit_code":        session.exitCodeValue(),
		"exit_error":       session.exitErrorText(),
	}

	if !running {
		globalShellSessionManager.delete(session.id)
	}

	return shellJSON(payload), nil
}

func shellWriteBackground(arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	sessionID := shellStringArg(arguments, "session_id", "")
	if sessionID == "" {
		return shellError("Missing required parameter: session_id"), nil
	}
	session := globalShellSessionManager.get(sessionID)
	if session == nil {
		return shellError(fmt.Sprintf("shell session not found: %s", sessionID)), nil
	}
	if !session.isRunning() {
		return shellError("shell session is no longer running"), nil
	}

	input := shellStringArg(arguments, "input", "")
	if input == "" {
		return shellError("Missing required parameter: input"), nil
	}

	n, err := session.writeString(input)
	if err != nil {
		return shellError(err.Error()), nil
	}

	return shellJSON(map[string]interface{}{
		"session_id":    session.id,
		"written_bytes": n,
		"message":       "Input written to shell session.",
	}), nil
}

func shellSubmitBackground(arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	input := shellStringArg(arguments, "input", "")
	if input == "" {
		return shellWriteBackground(map[string]interface{}{
			"session_id": arguments["session_id"],
			"input":      "\n",
		})
	}
	return shellWriteBackground(map[string]interface{}{
		"session_id": arguments["session_id"],
		"input":      input + "\n",
	})
}

func shellSendKeysBackground(arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	sessionID := shellStringArg(arguments, "session_id", "")
	if sessionID == "" {
		return shellError("Missing required parameter: session_id"), nil
	}
	session := globalShellSessionManager.get(sessionID)
	if session == nil {
		return shellError(fmt.Sprintf("shell session not found: %s", sessionID)), nil
	}
	if !session.isRunning() {
		return shellError("shell session is no longer running"), nil
	}

	keys := shellStringSliceArg(arguments, "keys")
	if len(keys) == 0 {
		return shellError("Missing required parameter: keys"), nil
	}

	var seq strings.Builder
	for _, key := range keys {
		part, err := shellKeySequence(key)
		if err != nil {
			return shellError(err.Error()), nil
		}
		seq.WriteString(part)
	}

	n, err := session.writeString(seq.String())
	if err != nil {
		return shellError(err.Error()), nil
	}

	return shellJSON(map[string]interface{}{
		"session_id":    session.id,
		"written_bytes": n,
		"message":       "Key sequence written to shell session.",
	}), nil
}

func shellResizeBackground(arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	sessionID := shellStringArg(arguments, "session_id", "")
	if sessionID == "" {
		return shellError("Missing required parameter: session_id"), nil
	}
	session := globalShellSessionManager.get(sessionID)
	if session == nil {
		return shellError(fmt.Sprintf("shell session not found: %s", sessionID)), nil
	}
	if !session.usePTY || session.pty == nil {
		return shellError("shell session is not running in pty mode"), nil
	}

	cols := shellPTYCols(arguments)
	rows := shellPTYRows(arguments)
	if err := shellResizePTY(session, cols, rows); err != nil {
		return shellError(err.Error()), nil
	}

	return shellJSON(map[string]interface{}{
		"session_id": session.id,
		"pty":        true,
		"cols":       cols,
		"rows":       rows,
		"message":    "Shell PTY resized.",
	}), nil
}

func shellStopBackground(arguments map[string]interface{}) (*protocol.CallToolResult, error) {
	sessionID := shellStringArg(arguments, "session_id", "")
	if sessionID == "" {
		return shellError("Missing required parameter: session_id"), nil
	}
	session := globalShellSessionManager.get(sessionID)
	if session == nil {
		return shellError(fmt.Sprintf("shell session not found: %s", sessionID)), nil
	}

	session.stop()
	globalShellSessionManager.delete(session.id)

	return shellJSON(map[string]interface{}{
		"session_id": session.id,
		"stopped":    true,
		"running":    false,
		"message":    "Shell session stopped.",
	}), nil
}

func shellBuildCommand(ctx context.Context, command string) (*exec.Cmd, error) {
	cmd := exec.CommandContext(ctx, shellPlatformShell(), shellPlatformShellArg(), command)
	cmd.Env = shellCommandEnv(false)
	shellSetProcessGroup(cmd)
	return cmd, nil
}

func shellBuildPtyCommand(ctx context.Context, command string) (gopty.Pty, *gopty.Cmd, error) {
	ptmx, err := gopty.New()
	if err != nil {
		return nil, nil, err
	}
	cmd := ptmx.CommandContext(ctx, shellPlatformShell(), shellPlatformShellArg(), command)
	cmd.Env = shellCommandEnv(true)
	shellSetProcessGroupPty(cmd)
	return ptmx, cmd, nil
}

func shellRunForeground(ctx context.Context, command string, workdir string, usePTY bool) (string, error) {
	if !usePTY {
		return shellRunForegroundCmd(ctx, command, workdir)
	}
	return shellRunForegroundPTY(ctx, command, workdir)
}

func shellRunForegroundCmd(ctx context.Context, command string, workdir string) (string, error) {
	cmd := exec.Command(shellPlatformShell(), shellPlatformShellArg(), command)
	cmd.Env = shellCommandEnv(false)
	shellSetProcessGroup(cmd)
	if workdir != "" {
		cmd.Dir = workdir
	}

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if startErr := cmd.Start(); startErr != nil {
		return fmt.Sprintf("Error: %s", startErr.Error()), startErr
	}

	waitDone := make(chan error, 1)
	go func() {
		waitDone <- cmd.Wait()
	}()

	select {
	case runErr := <-waitDone:
		stdoutStr := stdout.String()
		stderrStr := stderr.String()
		if runErr != nil {
			var parts []string
			parts = append(parts, fmt.Sprintf("Error: %s", runErr.Error()))
			if stderrStr != "" {
				parts = append(parts, fmt.Sprintf("Stderr:\n%s", stderrStr))
			}
			if stdoutStr != "" {
				parts = append(parts, fmt.Sprintf("Stdout:\n%s", stdoutStr))
			}
			return strings.Join(parts, "\n"), runErr
		}
		result := stdoutStr
		if stderrStr != "" {
			if result != "" {
				result = fmt.Sprintf("%s\nStderr:\n%s", result, stderrStr)
			} else {
				result = fmt.Sprintf("Stderr:\n%s", stderrStr)
			}
		}
		if result == "" {
			result = "(no output)"
		}
		return result, nil

	case <-ctx.Done():
		shellKillProcessGroup(cmd.Process)
		<-waitDone
		return "", ctx.Err()
	}
}

func shellRunForegroundPTY(ctx context.Context, command string, workdir string) (string, error) {
	ptmx, ptyCmd, err := shellBuildPtyCommand(ctx, command)
	if err != nil {
		return fmt.Sprintf("Error: %s", err.Error()), err
	}
	if workdir != "" {
		ptyCmd.Dir = workdir
	}
	if sizeErr := shellResizePtyRaw(ptmx, int(shellDefaultPTYCols), int(shellDefaultPTYRows)); sizeErr != nil {
		_ = ptmx.Close()
		return fmt.Sprintf("Error: %s", sizeErr.Error()), sizeErr
	}
	if err = ptyCmd.Start(); err != nil {
		_ = ptmx.Close()
		return fmt.Sprintf("Error: %s", err.Error()), err
	}

	var output bytes.Buffer
	copyDone := make(chan error, 1)
	go func() {
		_, copyErr := io.Copy(&output, ptmx)
		if copyErr != nil && !errors.Is(copyErr, io.EOF) {
			copyDone <- copyErr
			return
		}
		copyDone <- nil
	}()

	waitDone := make(chan error, 1)
	go func() {
		waitDone <- ptyCmd.Wait()
	}()

	select {
	case runErr := <-waitDone:
		_ = ptmx.Close()
		copyErr := <-copyDone
		if copyErr != nil && runErr == nil {
			runErr = copyErr
		}
		text := output.String()
		if text == "" {
			text = "(no output)"
		}
		if runErr != nil {
			return fmt.Sprintf("Error: %s\nOutput:\n%s", runErr.Error(), text), runErr
		}
		return text, nil

	case <-ctx.Done():
		shellKillProcessGroup(ptyCmd.Process)
		_ = ptmx.Close()
		<-waitDone
		<-copyDone
		return "", ctx.Err()
	}
}

func shellStringArg(arguments map[string]interface{}, key string, defaultValue string) string {
	value, ok := arguments[key].(string)
	if !ok {
		return defaultValue
	}
	value = strings.TrimSpace(value)
	if value == "" {
		return defaultValue
	}
	return value
}

func shellBoolArg(arguments map[string]interface{}, key string) bool {
	value, ok := arguments[key].(bool)
	return ok && value
}

func shellIntArg(arguments map[string]interface{}, key string, defaultValue int) int {
	value, ok := arguments[key]
	if !ok {
		return defaultValue
	}
	switch v := value.(type) {
	case int:
		return v
	case int64:
		return int(v)
	case float64:
		return int(v)
	default:
		return defaultValue
	}
}

func shellHasAction(arguments map[string]interface{}) bool {
	action, ok := arguments["action"].(string)
	if !ok {
		return false
	}
	switch strings.TrimSpace(action) {
	case "start", "poll", "write", "submit", "send_keys", "resize", "stop":
		return true
	default:
		return false
	}
}

func shellStringSliceArg(arguments map[string]interface{}, key string) []string {
	value, ok := arguments[key]
	if !ok {
		return nil
	}

	raw, ok := value.([]interface{})
	if !ok {
		return nil
	}

	result := make([]string, 0, len(raw))
	for _, item := range raw {
		text, ok := item.(string)
		if !ok {
			continue
		}
		text = strings.TrimSpace(text)
		if text == "" {
			continue
		}
		result = append(result, text)
	}
	return result
}

func shellPTYRows(arguments map[string]interface{}) uint16 {
	rows := shellIntArg(arguments, "rows", shellDefaultPTYRows)
	if rows <= 0 {
		rows = shellDefaultPTYRows
	}
	return uint16(rows)
}

func shellPTYCols(arguments map[string]interface{}) uint16 {
	cols := shellIntArg(arguments, "cols", shellDefaultPTYCols)
	if cols <= 0 {
		cols = shellDefaultPTYCols
	}
	return uint16(cols)
}

func shellPlatformShell() string {
	if runtime.GOOS == "windows" {
		return "cmd.exe"
	}
	return "sh"
}

func shellPlatformShellArg() string {
	if runtime.GOOS == "windows" {
		return "/C"
	}
	return "-c"
}

func shellCommandEnv(usePTY bool) []string {
	env := os.Environ()
	if usePTY {
		// This shell tool is optimized for reliable backend command execution rather
		// than rich terminal rendering. Keep PTY sessions in a plain terminal mode so
		// interactive CLIs are less likely to block on advanced terminal probing.
		env = shellEnsureEnv(env, "TERM", "dumb")
		env = shellEnsureEnv(env, "NO_COLOR", "1")
		env = shellEnsureEnv(env, "CLICOLOR", "0")
		env = shellEnsureEnv(env, "COLORTERM", "")
		env = shellEnsureEnv(env, "TERM_PROGRAM", "")
		env = shellEnsureEnv(env, "TERMINAL_EMULATOR", "")
		env = shellEnsureEnv(env, "PAGER", "cat")
	}
	return env
}

func shellEnsureEnv(env []string, key string, value string) []string {
	prefix := key + "="
	for i, entry := range env {
		if strings.HasPrefix(entry, prefix) {
			env[i] = prefix + value
			return env
		}
	}
	return append(env, prefix+value)
}

func shellKeySequence(key string) (string, error) {
	switch strings.ToLower(strings.TrimSpace(key)) {
	case "enter", "return":
		return "\n", nil
	case "tab":
		return "\t", nil
	case "backspace":
		return "\b", nil
	case "escape", "esc":
		return "\x1b", nil
	case "space":
		return " ", nil
	case "ctrl+c":
		return "\x03", nil
	case "ctrl+d":
		return "\x04", nil
	case "ctrl+z":
		return "\x1a", nil
	case "up":
		return "\x1b[A", nil
	case "down":
		return "\x1b[B", nil
	case "right":
		return "\x1b[C", nil
	case "left":
		return "\x1b[D", nil
	default:
		return "", fmt.Errorf("unsupported key sequence: %s", key)
	}
}

func shellStartPTYBackground(ctx context.Context, session *shellSession, arguments map[string]interface{}) error {
	ptmx, ptyCmd, err := shellBuildPtyCommand(ctx, session.command)
	if err != nil {
		return err
	}
	if session.workdir != "" {
		ptyCmd.Dir = session.workdir
	}
	if sizeErr := shellResizePtyRaw(ptmx, int(shellPTYCols(arguments)), int(shellPTYRows(arguments))); sizeErr != nil {
		_ = ptmx.Close()
		return sizeErr
	}
	if err = ptyCmd.Start(); err != nil {
		_ = ptmx.Close()
		return err
	}

	session.pty = ptmx
	session.ptyCmd = ptyCmd
	session.stdin = ptmx
	go session.capture(ptmx)
	return nil
}

func shellResizePTY(session *shellSession, cols uint16, rows uint16) error {
	if session.pty == nil {
		return fmt.Errorf("pty is not available for this shell session")
	}
	return shellResizePtyRaw(session.pty, int(cols), int(rows))
}

func shellResizePtyRaw(ptmx gopty.Pty, cols int, rows int) error {
	return ptmx.Resize(cols, rows)
}

func shellText(text string) *protocol.CallToolResult {
	return &protocol.CallToolResult{
		IsError: false,
		Content: []protocol.Content{
			&protocol.TextContent{Type: "text", Text: text},
		},
	}
}

func shellError(text string) *protocol.CallToolResult {
	return &protocol.CallToolResult{
		IsError: true,
		Content: []protocol.Content{
			&protocol.TextContent{Type: "text", Text: text},
		},
	}
}

func shellJSON(v interface{}) *protocol.CallToolResult {
	bs, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return shellError(err.Error())
	}
	return &protocol.CallToolResult{
		IsError: false,
		Content: []protocol.Content{
			&protocol.TextContent{Type: "text", Text: string(bs)},
		},
	}
}

func newShellRingBuffer(maxBytes int) *shellRingBuffer {
	return &shellRingBuffer{
		buf:      make([]byte, 0, maxBytes),
		maxBytes: maxBytes,
	}
}

func (b *shellRingBuffer) write(chunk []byte) {
	b.mu.Lock()
	defer b.mu.Unlock()

	b.buf = append(b.buf, chunk...)
	if len(b.buf) <= b.maxBytes {
		return
	}

	overflow := len(b.buf) - b.maxBytes
	b.buf = append([]byte{}, b.buf[overflow:]...)
	b.truncated = true
	if b.readPos >= overflow {
		b.readPos -= overflow
	} else {
		b.readPos = 0
	}
}

func (b *shellRingBuffer) readUnread(limit int) (string, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()

	if b.readPos >= len(b.buf) {
		return "", false
	}
	end := len(b.buf)
	truncated := false
	if limit > 0 && b.readPos+limit < end {
		end = b.readPos + limit
		truncated = true
	}
	text := string(b.buf[b.readPos:end])
	b.readPos = end
	return text, truncated || b.truncated
}

func (s *shellSession) capture(r io.Reader) {
	buffer := make([]byte, 4096)
	for {
		n, err := r.Read(buffer)
		if n > 0 {
			s.output.write(buffer[:n])
		}
		if err != nil {
			return
		}
	}
}

func (s *shellSession) wait() {
	defer close(s.done)
	if s.usePTY && s.ptyCmd != nil {
		s.exitErr = s.ptyCmd.Wait()
		if s.ptyCmd.ProcessState != nil {
			code := s.ptyCmd.ProcessState.ExitCode()
			s.exitCode = &code
		}
		s.closeInput()
		return
	}

	s.exitErr = s.cmd.Wait()
	if s.cmd.ProcessState != nil {
		code := s.cmd.ProcessState.ExitCode()
		s.exitCode = &code
	}
	s.closeInput()
}

func (s *shellSession) isRunning() bool {
	select {
	case <-s.done:
		return false
	default:
		return true
	}
}

func (s *shellSession) processID() int {
	if s.usePTY && s.ptyCmd != nil && s.ptyCmd.Process != nil {
		return s.ptyCmd.Process.Pid
	}
	if s.cmd == nil || s.cmd.Process == nil {
		return 0
	}
	return s.cmd.Process.Pid
}

func (s *shellSession) exitCodeValue() interface{} {
	if s.exitCode == nil {
		return nil
	}
	return *s.exitCode
}

func (s *shellSession) exitErrorText() interface{} {
	if s.exitErr == nil {
		return nil
	}
	return s.exitErr.Error()
}

func (s *shellSession) stop() {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.closed = true

	if s.cancel != nil {
		s.cancel()
	}
	s.closeInputLocked()
	done := s.done
	var process *os.Process
	if s.usePTY && s.ptyCmd != nil {
		process = s.ptyCmd.Process
	} else if s.cmd != nil {
		process = s.cmd.Process
	}
	s.mu.Unlock()

	// Wait outside the lock so wait() can acquire it to call closeInput().
	if process == nil {
		return
	}
	if err := process.Signal(os.Interrupt); err != nil {
		shellKillProcessGroup(process)
		return
	}
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		shellKillProcessGroup(process)
	}
}

func (s *shellSession) closeInput() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.closeInputLocked()
}

func (s *shellSession) closeInputLocked() {
	if s.pty != nil {
		_ = s.pty.Close()
		s.pty = nil
		s.stdin = nil // stdin is the same PTY handle in PTY mode.
		return
	}
	if s.stdin != nil {
		_ = s.stdin.Close()
		s.stdin = nil
	}
}

func (s *shellSession) writeString(input string) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.closed || s.stdin == nil {
		return 0, fmt.Errorf("shell session is no longer running")
	}
	return io.WriteString(s.stdin, input)
}

func (m *shellSessionManager) nextSessionID() string {
	id := atomic.AddUint64(&m.nextID, 1)
	return "shell-" + strconv.FormatUint(id, 10)
}

func (m *shellSessionManager) put(session *shellSession) {
	m.ensureCleanup()
	m.mu.Lock()
	defer m.mu.Unlock()
	session.lastActivity = time.Now()
	m.sessions[session.id] = session
}

func (m *shellSessionManager) get(id string) *shellSession {
	m.mu.Lock()
	defer m.mu.Unlock()
	session := m.sessions[id]
	if session != nil {
		session.lastActivity = time.Now()
	}
	return session
}

func (m *shellSessionManager) delete(id string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.sessions, id)
}

func (m *shellSessionManager) ensureCleanup() {
	m.cleanupOnce.Do(func() {
		go m.cleanupLoop()
	})
}

func (m *shellSessionManager) cleanupLoop() {
	ticker := time.NewTicker(time.Minute)
	defer ticker.Stop()
	for range ticker.C {
		m.evictIdle()
	}
}

func (m *shellSessionManager) evictIdle() {
	m.mu.Lock()
	var expired []*shellSession
	for _, session := range m.sessions {
		if time.Since(session.lastActivity) > shellSessionIdleTimeout {
			expired = append(expired, session)
		}
	}
	for _, session := range expired {
		delete(m.sessions, session.id)
	}
	m.mu.Unlock()

	for _, session := range expired {
		session.stop()
	}
}

```
