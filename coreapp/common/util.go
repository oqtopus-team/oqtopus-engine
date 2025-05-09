package common

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"

	"go.uber.org/zap"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	api "github.com/oqtopus-team/oqtopus-engine/coreapp/oas/gen/providerapi"
)

func GetAssetAbsPath(fileName string) (string, error) {
	return GetAbsPath(fileName, "assets")
}

func GetAbsPath(fileName, dirName string) (string, error) {
	_, cFilePath, _, ok := runtime.Caller(0)
	if !ok {
		return "", fmt.Errorf("runtime.Caller error")
	}
	dir := filepath.Dir(cFilePath)
	path := fmt.Sprintf("%s/%s/%s", dir, dirName, fileName)
	_, err := os.Stat(path)
	if err != nil {
		return "", err
	}
	return path, nil
}

func GetAsset(filename string) (string, error) {
	path, err := GetAssetAbsPath(filename)
	if err != nil {
		return "", err
	}
	return ReadFile(path)
}

func ReadFile(filepath string) (string, error) {
	bytes, err := os.ReadFile(filepath)
	if err != nil {
		return "", err
	}
	return string(bytes), nil
}

// --- API Client Utilities ---

// SecuritySource provides API key authentication for the generated OpenAPI client.
type SecuritySource struct {
	apiKey string
}

// ApiKeyAuth implements the api.SecuritySource interface.
func (p SecuritySource) ApiKeyAuth(ctx context.Context, name string) (api.ApiKeyAuth, error) {
	apiKeyAuth := api.ApiKeyAuth{}
	apiKeyAuth.SetAPIKey(p.apiKey)
	return apiKeyAuth, nil
}

// NewSecuritySource creates a new SecuritySource instance.
// This constructor is needed because the apiKey field is unexported.
func NewSecuritySource(apiKey string) SecuritySource {
	return SecuritySource{apiKey: apiKey}
}

// NewAPIClient creates a new OpenAPI client with the given endpoint and API key.
// It uses the SecuritySource for authentication.
func NewAPIClient(endpoint, apiKey string) (*api.Client, error) {
	ss := SecuritySource{apiKey: apiKey}
	// Use the default http.Client via ogen's default behavior
	cli, err := api.NewClient(endpoint, ss)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to create a new API client/endpoint:%s/reason:%s", endpoint, err))
		return nil, err
	}
	return cli, nil
}

// --- End API Client Utilities ---

// TODO optimize
func ContainsStatementName(s string, list []string) bool {
	s = NormalizeStatementName(s)
	for _, c := range list {
		if s == NormalizeStatementName(c) {
			return true
		}
	}
	return false
}

func NormalizeStatementName(s string) string {
	s = strings.ToLower(s)
	s = strings.ReplaceAll(s, "_", "")
	s = strings.ReplaceAll(s, "-", "")
	h := strings.TrimSuffix(s, "statement")
	return h
}

func ValidAddress(host, port string) (string, error) {
	hostPattern := regexp.MustCompile(`^([0-9a-zA-Z_-]|\.)+$`)
	if !hostPattern.MatchString(host) {
		return "", fmt.Errorf("%s is an invalid host name", host)
	}
	portPattern := regexp.MustCompile(`^[0-9]+$`)
	if !portPattern.MatchString(port) {
		return "", fmt.Errorf("%s is an invalid port number", port)
	}
	num, err := strconv.Atoi(port)
	if err != nil {
		return "", err
	}
	if num < 0 || num > 65535 {
		return "", fmt.Errorf("%d is not a port number within the allowed range", num)
	}
	return fmt.Sprintf("%s:%s", host, port), nil
}

// TODO: remove this function because grpc.DialContext is deprecated
func GRPCConnection(address string, timeout time.Duration, useCred bool) (*grpc.ClientConn, error) {
	ctx, _ := context.WithTimeout(context.Background(), timeout)
	if useCred {
		return grpc.DialContext(
			ctx,
			address,
			grpc.WithTransportCredentials(insecure.NewCredentials()),
			grpc.WithBlock(),
		)
	} else {
		return grpc.DialContext(ctx, address, grpc.WithInsecure())
	}
}

// For ad hoc JSON printing for logging
func PlainJsonString(jsonInput string) string {
	if jsonInput[0] == '"' {
		jsonInput = jsonInput[1:]
	}
	if jsonInput[len(jsonInput)-1] == '"' {
		jsonInput = jsonInput[:len(jsonInput)-1]
	}
	jsonInput = strings.ReplaceAll(jsonInput, "\n", "")
	jsonInput = strings.ReplaceAll(jsonInput, "\\\"", "\"")
	jsonInput = strings.ReplaceAll(jsonInput, " ", "")
	return jsonInput
}

func IsDirWritable(dirPath string) error {
	info, err := os.Stat(dirPath)
	if os.IsNotExist(err) {
		return fmt.Errorf("directory does not exist: %s", dirPath)
	}
	if err != nil {
		return err
	}

	if !info.IsDir() {
		return fmt.Errorf("%s is not a directory", dirPath)
	}

	tempFile, err := os.CreateTemp(dirPath, "test-write-*.tmp")
	if err != nil {
		return fmt.Errorf("write permission denied for directory: %s", dirPath)
	}
	fileName := tempFile.Name()
	tempFile.Close()

	if err := os.Remove(fileName); err != nil {
		return fmt.Errorf("failed to remove temporary file: %s", err)
	}

	return nil
}

func ReadSettingsFile(settingsPath string) (string, error) {
	bytes, err := os.ReadFile(settingsPath)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to read settings file/path:%s/reason:%s",
			settingsPath, err))
		if absolutePath, err := filepath.Abs(settingsPath); err != nil {
			zap.L().Error(fmt.Sprintf("failed to get absolute path of %s/reason:%s",
				settingsPath, err))
		} else {
			zap.L().Debug(fmt.Sprintf("absolute path:%s", absolutePath))
		}
		return "", err
	}
	return string(bytes), nil
}
