package core

import (
	"fmt"

	"github.com/BurntSushi/toml"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/common"
	"go.uber.org/zap"
)

type MitigatorSetting struct {
	Host string `toml:"host"`
	Port string `toml:"port"`
}

func NewMitigatorSetting() MitigatorSetting {
	return MitigatorSetting{
		Host: "localhost",
		Port: "5011",
	}
}

var globalSetting *Setting

// TODO: Do not use interface{} for setting value. Use specific struct type for each setting.
type Setting struct {
	ComponentSetting map[string]interface{} `toml:"com,omitempty"`
	RunGroupSetting  map[string]interface{} `toml:"run_group,omitempty"` // This is a placeholder for future use
}

func ResetSetting() {
	globalSetting = &Setting{
		ComponentSetting: make(map[string]interface{}),
		RunGroupSetting:  make(map[string]interface{}),
	}
	return
}

func RegisterSetting(settingName string, settingVal interface{}) {
	globalSetting.ComponentSetting[settingName] = settingVal
}

func ParseSettingFromPath(settingsPath string) error {
	tomlString, err := common.ReadSettingsFile(settingsPath)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to read setting file/reason:%s", err))
		return err
	}
	return globalSetting.parseSetting(tomlString)
}

func GetGlobalSetting() *Setting {
	return globalSetting
}

func GetComponentSetting(name string) (interface{}, bool) {
	if globalSetting == nil {
		zap.L().Error("Setting is not initialized")
		return nil, false
	}
	val, ok := globalSetting.ComponentSetting[name]
	return val, ok
}

func newSetting() *Setting {
	return &Setting{
		ComponentSetting: make(map[string]interface{}),
		RunGroupSetting:  make(map[string]interface{}),
	}
}

func (s *Setting) registerSetting(settingName string, settingVal interface{}) {
	s.ComponentSetting[settingName] = settingVal
}

func (s *Setting) parseSetting(tomlString string) error {
	_, err := toml.Decode(tomlString, s)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to parse setting/reason:%s", err))
		return err
	}
	zap.L().Debug(fmt.Sprintf("Setting is %v", s.ComponentSetting))
	return nil
}
