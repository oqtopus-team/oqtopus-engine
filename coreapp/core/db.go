package core

import (
	"fmt"
	"sync"

	"go.uber.org/zap"
)

type MemoryDB struct {
	dbMap  map[string]Job
	dbChan <-chan Job
	mu     sync.RWMutex
}

func (d *MemoryDB) Setup(dbc DBChan, c *Conf) error {
	d.dbMap = make(map[string]Job)
	d.dbChan = dbc
	go func() {
		for {
			job := <-d.dbChan
			if job == nil { //when dbChan is closed
				return //TODO :remove this adhoc code. Use RunGroup
			}
			zap.L().Debug(fmt.Sprintf("[MemoryDB] Received %s", job.JobData().ID))
			if err := d.Update(job); err != nil {
				zap.L().Error(fmt.Sprintf("failed to update a job(%s). Reason:%s",
					job.JobData().ID, err.Error()))
			}
		}
	}()
	return nil
}

func (d *MemoryDB) Insert(j Job) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.dbMap[j.JobData().ID] = j
	return nil
}

func (d *MemoryDB) Get(jobID string) (Job, error) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if val, ok := d.dbMap[jobID]; ok {
		return val, nil
	}
	err := fmt.Errorf("not found %s", jobID)
	zap.L().Info("[MemoryDB]", zap.Field(zap.Error(err)))
	return &NormalJob{}, err
}

func (d *MemoryDB) Update(j Job) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.dbMap[j.JobData().ID] = j
	return nil
}

func (d *MemoryDB) Delete(jobID string) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if _, ok := d.dbMap[jobID]; ok {
		delete(d.dbMap, jobID)
		zap.L().Info(fmt.Sprintf("[MemoryDB] deleted %s from DB", jobID))
		return nil
	}
	err := fmt.Errorf("failed to find %s", jobID)
	zap.L().Info("[MemoryDB]", zap.Field(zap.Error(err)))
	return err
}

func (d *MemoryDB) UpdateQASM(jobID string, qasm_str string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	job := d.dbMap[jobID]
	job.JobData().QASM = qasm_str
	d.dbMap[jobID] = job
}
