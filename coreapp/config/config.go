package config

import (
	"fmt"

	"github.com/BurntSushi/toml"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/common"

	// Removed component package imports
	"go.uber.org/zap"
)

// --- Configuration Interfaces ---

// GatewayConfig defines the interface for accessing Gateway agent settings.
type GatewayConfig interface {
	GetGatewayHost() string
	GetGatewayPort() string
	GetAPIEndpoint() string
	GetAPIKey() string
	GetDeviceID() string
}

// TranquConfig defines the interface for accessing Tranqu transpiler settings.
type TranquConfig interface {
	GetHost() string
	GetPort() string
}

// EstimationConfig defines the interface for accessing Estimation service settings.
type EstimationConfig interface {
	GetHost() string
	GetPort() string
	GetBasisGates() []string
}

// MitigatorConfig defines the interface for accessing Mitigator service settings.
type MitigatorConfig interface {
	GetHost() string
	GetPort() string
}

// --- CurrentRunConfig Struct ---

// CurrentRunConfig holds the configuration interfaces for the current run.
// The actual implementations are provided externally (e.g., in main.go).
type CurrentRunConfig struct {
	Gateway    GatewayConfig    `toml:"gateway"`    // Interface type
	Tranqu     TranquConfig     `toml:"tranqu"`     // Interface type
	Estimation EstimationConfig `toml:"estimation"` // Interface type
	Mitigator  MitigatorConfig  `toml:"mitigator"`  // Interface type
	// Add other component config interfaces here as needed
}

// globalCurrentRunConfig holds the singleton instance of the CurrentRunConfig.
// It is now expected to be set externally via SetGlobalCurrentRunConfig.
var globalCurrentRunConfig *CurrentRunConfig

// SetGlobalCurrentRunConfig sets the global configuration instance.
// This should be called once at application startup after creating the initial config.
func SetGlobalCurrentRunConfig(cfg *CurrentRunConfig) {
	if globalCurrentRunConfig != nil {
		zap.L().Warn("Global CurrentRunConfig is being overwritten. This should typically happen only once.")
	}
	globalCurrentRunConfig = cfg
	zap.L().Debug("Global CurrentRunConfig has been set.")
}

// ParseCurrentRunConfigFromPath reads a TOML file from the given path and merges it into the global configuration.
// It overwrites default values with those found in the TOML file.
// The global config MUST be set via SetGlobalCurrentRunConfig before calling this.
func ParseCurrentRunConfigFromPath(settingsPath string) error {
	if globalCurrentRunConfig == nil {
		// This indicates an initialization order issue. SetGlobalCurrentRunConfig must be called first.
		err := fmt.Errorf("global config not set before calling ParseCurrentRunConfigFromPath")
		zap.L().Error(err.Error())
		return err
	}

	tomlString, err := common.ReadSettingsFile(settingsPath)
	if err != nil {
		zap.L().Warn(fmt.Sprintf("failed to read setting file '%s', proceeding with defaults/previously loaded settings: %s", settingsPath, err))
		// Do not return error here, allow running with defaults if file is missing/unreadable
		return nil
	}

	// Decode the TOML string into the global config instance.
	// This will overwrite default values with values from the file.
	if _, err := toml.Decode(tomlString, globalCurrentRunConfig); err != nil {
		zap.L().Error(fmt.Sprintf("failed to parse setting file '%s': %s", settingsPath, err))
		return fmt.Errorf("failed to parse settings file '%s': %w", settingsPath, err) // Return error on parse failure
	}

	zap.L().Info(fmt.Sprintf("Successfully parsed and applied settings from '%s'", settingsPath))
	zap.L().Debug(fmt.Sprintf("CurrentRunConfig loaded: %+v", globalCurrentRunConfig)) // Be mindful of logging sensitive info
	return nil
}

// GetCurrentRunConfig returns the global instance of CurrentRunConfig.
// It's recommended to call SetGlobalCurrentRunConfig and ParseCurrentRunConfigFromPath before accessing the config.
func GetCurrentRunConfig() *CurrentRunConfig {
	if globalCurrentRunConfig == nil {
		// This case should ideally not happen if initialization is done correctly at startup via SetGlobalCurrentRunConfig.
		zap.L().Fatal("Attempted to get CurrentRunConfig before it was set. Application cannot proceed.")
	}
	return globalCurrentRunConfig
}

// Removed NewDefaultCurrentRunConfig and InitGlobalCurrentRunConfig
// Initialization is now handled externally (e.g., in main.go) by creating
// a CurrentRunConfig instance using component constructors and then calling SetGlobalCurrentRunConfig.
