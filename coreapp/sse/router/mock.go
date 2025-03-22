package router

import (
	"fmt"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"go.uber.org/dig"
)

type successQPUForTest struct {
	core.UnimplementedQPU
}

func (successQPUForTest) Send(j core.Job) error {
	j.JobData().Result.Counts = map[string]uint32{"00": 400, "11": 600}
	j.JobData().Result.TranspilerInfo.PhysicalVirtualMapping = map[uint32]uint32{0: 1, 1: 0}
	j.JobData().Result.Message = "dummysuccessresult"
	j.JobData().Status = core.SUCCEEDED
	return nil
}

type failQPUForTest struct {
	core.UnimplementedQPU
}

func (failQPUForTest) Send(j core.Job) error {
	j.JobData().Result.Message = "dummyfailureresult"
	j.JobData().Status = core.FAILED
	return nil
}

type errorQPUForTest struct {
	core.UnimplementedQPU
}

func (errorQPUForTest) Send(j core.Job) error {
	return fmt.Errorf("QPU error")
}

type successTranspilerForTest struct{}

func (successTranspilerForTest) IsAcceptableTranspilerLib(string) bool {
	return true
}
func (successTranspilerForTest) Setup(*core.Conf) error { return nil }
func (successTranspilerForTest) GetHealth() error       { return nil }
func (successTranspilerForTest) Transpile(j core.Job) error {
	j.JobData().TranspiledQASM = "transpiled QASM"
	return nil
}
func (successTranspilerForTest) TearDown() {}

type failTranspilerForTest struct{}

func (failTranspilerForTest) IsAcceptableTranspilerLib(string) bool { return true }
func (failTranspilerForTest) Setup(*core.Conf) error                { return nil }
func (failTranspilerForTest) GetHealth() error                      { return nil }
func (failTranspilerForTest) Transpile(core.Job) error              { return fmt.Errorf("Transpile Error") }
func (failTranspilerForTest) TearDown()                             {}

func getContainer(transpiler core.Transpiler, qpu core.QPUManager) *dig.Container {
	s := core.NewSystemComponents(dig.New())
	c := s.Container
	c.Provide(func() core.Transpiler { return transpiler })
	c.Provide(func() core.QPUManager { return qpu })
	return c
}
