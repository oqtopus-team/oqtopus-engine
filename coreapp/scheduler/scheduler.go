package scheduler

import (
	"fmt"
	"sync"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"go.uber.org/zap"
)

type statusManager interface {
	Update(job core.Job, status core.Status)
	Delete(jobID string)
	Get(jobID string) []core.Status
}

// for production
type prodStatusManager struct{}

func (p *prodStatusManager) Update(job core.Job, status core.Status) {
	job.JobData().Status = status
}
func (p *prodStatusManager) Delete(jobID string) {}
func (p *prodStatusManager) Get(jobID string) []core.Status {
	return nil
}

type NormalScheduler struct {
	queue         *NormalQueue
	statusManager statusManager
}

type jobInScheduler struct {
	job      core.Job
	finished *sync.WaitGroup
}

func (n *NormalScheduler) Setup(conf *core.Conf) error {
	n.queue = &NormalQueue{}
	n.queue.Setup(conf)
	n.statusManager = &prodStatusManager{}
	return nil
}

// TODO: use rungroup
func (n *NormalScheduler) Start() error {
	// TODO: functionalize
	go func() {
		for {
			zap.L().Debug("checking the queue...")
			jis, err := n.queue.Dequeue(true)
			if err != nil {
				zap.L().Error(fmt.Sprintf("failed to get job from queue. Reason:%s", err))
				continue
			}
			jid := jis.job.JobData().ID
			zap.L().Debug(fmt.Sprintf("processing job:%s", jid))

			func() {
				defer func() {
					if r := recover(); r != nil {
						zap.L().Error("recovered from panic in scheduler start", zap.String("jobID", jid), zap.Any("panic", r))
						jis.job.JobData().Status = core.FAILED
						jis.job.JobContext().DBChan <- jis.job.Clone()
					}
					jis.finished.Done()
				}()

				// TODO: not update status in scheduler
				st := core.RUNNING
				n.statusManager.Update(jis.job, st)
				jis.job.JobContext().DBChan <- jis.job.Clone()
				jis.job.Process()
				zap.L().Debug(fmt.Sprintf("finished to process job(%s), status:%s", jid, jis.job.JobData().Status))
			}()
		}
	}()
	// TODO connected Channel
	return nil
}

func (n *NormalScheduler) HandleJob(j core.Job) {
	zap.L().Debug(fmt.Sprintf("starting to handle job(%s) in %s", j.JobData().ID, j.JobData().Status))
	go func() {
		defer func() {
			if r := recover(); r != nil {
				zap.L().Error("recovered from panic in handle job", zap.String("jobID", j.JobData().ID), zap.Any("panic", r))
			}
		}()
		defer func() {
			zap.L().Debug(fmt.Sprintf("status history job(%s): %v", j.JobData().ID, n.statusManager.Get(j.JobData().ID)))
			n.statusManager.Delete(j.JobData().ID)
		}()
		n.handleImpl(j)
	}()
}

func (n *NormalScheduler) HandleJobForTest(j core.Job, wg *sync.WaitGroup) {
	go func() {
		defer wg.Done()
		n.handleImpl(j)
	}()
}

func (n *NormalScheduler) handleImpl(j core.Job) {
	defer func() {
		if r := recover(); r != nil {
			zap.L().Error("recovered from panic in handle impl", zap.String("jobID", j.JobData().ID), zap.Any("panic", r))
			n.statusManager.Update(j, core.FAILED)
			j.JobContext().DBChan <- j.Clone()
		}
	}()

	for {
		jid := j.JobData().ID
		j.JobData().UseJobInfoUpdate = false //very adhoc
		st := j.JobData().Status             // must be ready
		n.statusManager.Update(j, st)
		zap.L().Debug(fmt.Sprintf("handling job(%s)in %s starting", jid, st))
		if j.JobData().Status != core.READY {
			zap.L().Error(
				fmt.Sprintf("finished to handle job(%s) with unexpected status:%s", jid, j.JobData().Status.String()))
			// not write to DB
			return
		}
		zap.L().Debug(fmt.Sprintf("handling job(%s). start pre-processing", jid))
		j.PreProcess()
		if j.IsFinished() {
			j.JobData().UseJobInfoUpdate = true //TODO: fix this adhoc
		}
		j.JobContext().DBChan <- j.Clone()
		if j.IsFinished() {
			zap.L().Debug(fmt.Sprintf("finished to handle job(%s) after pre-processing", jid))
			n.statusManager.Update(j, j.JobData().Status)
			return
		}
		var wg sync.WaitGroup
		wg.Add(1)
		jis := &jobInScheduler{
			job:      j,
			finished: &wg,
		}
		n.queue.queueChan <- jis
		wg.Wait()                           // wait for processing
		j.JobData().UseJobInfoUpdate = true //TODO: fix this adhoc
		zap.L().Debug(fmt.Sprintf("Processed Job Status: %s", j.JobData().Status))
		if j.IsFinished() {
			j.JobContext().DBChan <- j.Clone()
			zap.L().Debug(fmt.Sprintf("finished to handle job(%s) after processing with status:%s",
				jid, j.JobData().Status.String()))
			n.statusManager.Update(j, j.JobData().Status)
			j.JobContext().DBChan <- j.Clone()
			return
		}
		zap.L().Debug(fmt.Sprintf("handling job(%s). start post-processing", jid))
		j.PostProcess()
		if j.IsFinished() {
			zap.L().Debug(fmt.Sprintf("finished to handle job(%s) after post-processing with status:%s",
				jid, j.JobData().Status.String()))
			n.statusManager.Update(j, j.JobData().Status)
			j.JobContext().DBChan <- j.Clone()
			return
		}
		zap.L().Debug(fmt.Sprintf("one more loop for job(%s)", jid))
	}
}

func (n *NormalScheduler) GetCurrentQueueSize() int {
	return n.queue.fifo.GetLen()
}

func (n *NormalScheduler) IsOverRefillThreshold() bool {
	return n.queue.refillThreshold <= n.queue.fifo.GetLen()
}
