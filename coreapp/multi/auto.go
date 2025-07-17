package multi

import (
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"go.uber.org/zap"
)

const MultiAutoServerName = "multi_auto_server"

type MultiAutoServerImpl struct{}

func (m *MultiAutoServerImpl) Setup() error {
	return nil
}

func (m *MultiAutoServerImpl) GetEmptyParams() interface{} {
	return m
}

func (m *MultiAutoServerImpl) SetParams(p interface{}) error {
	return nil
}

func (m *MultiAutoServerImpl) Start() error {
	zap.L().Info("MultiAuto Starting")
	return nil
}

func (m *MultiAutoServerImpl) Cleanup() {
	zap.L().Info("MultiAuto Cleaning up")
}

func (m *MultiAutoServerImpl) Handle(j core.Job) error {
	zap.L().Debug("MultiAuto Handling Job")
	return nil
}
