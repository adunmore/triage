import logging
import time
from triage.component.catwalk.utils import Batch
from triage.util.db import run_statements
from triage.experiments import ExperimentBase

try:
    from rq import Queue
except ImportError:
    print(
        "rq not available. To use RQExperiment, install triage with the RQ extension: "
        "pip install triage[rq]"
    )
    raise


DEFAULT_TIMEOUT = (
    "365d"
)  # We want to basically invalidate RQ's timeouts by setting them each to one year


class RQExperiment(ExperimentBase):
    """An experiment that uses the python-rq library to enqueue tasks and wait for them to finish.

    http://python-rq.org/

    For this experiment to complete, you need some amount of RQ workers running the Triage codebase
    (either on the same machine as the experiment or elsewhere),
    and a Redis instance that both the experiment process and RQ workers can access.

    Args:
        redis_connection (redis.connection): A connection to a Redis instance that
            some rq workers can also access
        sleep_time (int, default 5) How many seconds the process should sleep while
            waiting for RQ results
        queue_kwargs (dict, default {}) Any extra keyword arguments to pass to Queue creation
    """

    def __init__(
        self, redis_connection, sleep_time=5, queue_kwargs=None, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.redis_connection = redis_connection
        if queue_kwargs is None:
            queue_kwargs = {}
        self.queue = Queue(connection=self.redis_connection, **queue_kwargs)
        self.sleep_time = sleep_time

    def wait_for(self, jobs):
        """Wait for a list of jobs to complete

        Will run until all jobs are either finished or failed.

        Args:
            jobs (list of rq.Job objects)

        Returns: (list) of job return values
        """
        while True:
            num_done = sum(1 for job in jobs if job.is_finished)
            num_failed = sum(1 for job in jobs if job.is_failed)
            num_pending = sum(
                1 for job in jobs if not job.is_finished and not job.is_failed
            )
            logging.info(
                "Report: jobs %s done, %s failed, %s pending",
                num_done,
                num_failed,
                num_pending,
            )
            if num_pending == 0:
                logging.info("All jobs completed or failed, returning")
                return [job.result for job in jobs]
            else:
                logging.info("Sleeping for %s seconds", self.sleep_time)
                time.sleep(self.sleep_time)

    def process_inserts(self, inserts):
        insert_batches = [
            list(task_batch) for task_batch in Batch(inserts, 25)
        ]
        jobs = [
            self.queue.enqueue(
                run_statements,
                insert_batch,
                self.db_engine,
                timeout=DEFAULT_TIMEOUT,
                result_ttl=DEFAULT_TIMEOUT,
                ttl=DEFAULT_TIMEOUT,
            )
            for insert_batch in insert_batches
        ]
        self.wait_for(jobs)

    def process_matrix_build_tasks(self, matrix_build_tasks):
        """Run matrix build tasks using RQ

        Args:
            matrix_build_tasks (dict) Keys should be matrix uuids (though not used here),
                values should be dictionaries suitable as kwargs for sending
                to self.matrix_builder.build_matrix

        Returns: (list) of job results for each given task
        """
        jobs = [
            self.queue.enqueue(
                self.matrix_builder.build_matrix,
                timeout=DEFAULT_TIMEOUT,
                result_ttl=DEFAULT_TIMEOUT,
                ttl=DEFAULT_TIMEOUT,
                **build_task
            )
            for build_task in matrix_build_tasks.values()
        ]
        return self.wait_for(jobs)

    def process_train_test_tasks(self, train_test_tasks):
        """Run train tasks using RQ

        Args:
            train_tasks (list) of dictionaries, each representing kwargs suitable
                for self.trainer.process_train_task
        Returns: (list) of job results for each given task
        """
        jobs = [
            self.queue.enqueue(
                self.model_train_tester.process_task,
                timeout=DEFAULT_TIMEOUT,
                result_ttl=DEFAULT_TIMEOUT,
                ttl=DEFAULT_TIMEOUT,
                **task
            )
            for task in train_test_tasks
        ]
        return self.wait_for(jobs)

    def process_subset_tasks(self, subset_tasks):
        """Run subset tasks using RQ

        Args:
            subset_tasks (list) of dictionaries, each representing kwargs suitable
                for self.subsetter.process_task
        Returns: (list) of job results for each given task
        """
        jobs = [
            self.queue.enqueue(
                self.subsetter.process_task,
                timeout=DEFAULT_TIMEOUT,
                result_ttl=DEFAULT_TIMEOUT,
                ttl=DEFAULT_TIMEOUT,
                **task
            )
            for task in subset_tasks
        ]
        return self.wait_for(jobs)
