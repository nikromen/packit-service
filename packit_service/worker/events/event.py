# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Generic/abstract event classes.
"""
import copy
import inspect
from datetime import datetime, timezone
from logging import getLogger
from typing import Dict, Iterable, Optional, Type, Union, Set, List, Any

from ogr.abstract import GitProject
from packit.config import JobConfigTriggerType, PackageConfig

import packit_service.worker.events
from packit_service.config import PackageConfigGetter, ServiceConfig
from packit_service.models import CoprBuildTargetModel, TFTTestRunTargetModel

from packit_service.service.db_triggers import (
    _AddDbTrigger,
    AddReleaseDbTrigger,
    AddPullRequestDbTrigger,
    AddBranchPushDbTrigger,
    AddIssueDbTrigger,
)
from packit_service.models import AbstractTriggerDbType

logger = getLogger(__name__)


AbstractAddDbTrigger = Union[
    AddReleaseDbTrigger,
    AddPullRequestDbTrigger,
    AddIssueDbTrigger,
    AddBranchPushDbTrigger,
]


MAP_EVENT_TO_JOB_CONFIG_TRIGGER_TYPE: Dict[Type["Event"], JobConfigTriggerType] = {}


def use_for_job_config_trigger(trigger_type: JobConfigTriggerType):
    """
    [class decorator]
    Specify a trigger_type which this event class matches
    so we don't need to search database to get that information.

    In other words, what job-config in the configuration file
    is compatible with this event.

    Example:
    ```
    @use_for_job_config_trigger(trigger_type=JobConfigTriggerType.commit)
    class KojiBuildEvent(AbstractKojiEvent):
    ```
    """

    def _add_to_mapping(kls: Type["Event"]):
        MAP_EVENT_TO_JOB_CONFIG_TRIGGER_TYPE[kls] = trigger_type
        return kls

    return _add_to_mapping


class EventData:
    """
    Class to represent the data which are common for handlers and comes from the original event
    """

    def __init__(
        self,
        event_type: str,
        actor: str,
        trigger_id: int,
        project_url: str,
        tag_name: Optional[str],
        git_ref: Optional[str],
        pr_id: Optional[int],
        commit_sha: Optional[str],
        identifier: Optional[str],
        event_dict: Optional[dict],
        issue_id: Optional[int],
        task_accepted_time: Optional[datetime],
        build_targets_override: Optional[List[str]],
        tests_targets_override: Optional[List[str]],
        branches_override: Optional[List[str]],
    ):
        self.event_type = event_type
        self.actor = actor
        self.trigger_id = trigger_id
        self.project_url = project_url
        self.tag_name = tag_name
        self.git_ref = git_ref
        self.pr_id = pr_id
        self.commit_sha = commit_sha
        self.identifier = identifier
        self.event_dict = event_dict
        self.issue_id = issue_id
        self.task_accepted_time = task_accepted_time
        self.build_targets_override = (
            set(build_targets_override) if build_targets_override else None
        )
        self.tests_targets_override = (
            set(tests_targets_override) if tests_targets_override else None
        )
        self.branches_override = set(branches_override) if branches_override else None

        # lazy attributes
        self._project = None
        self._repo_namespace = ""
        self._repo_name = ""
        self._db_trigger: Optional[AbstractTriggerDbType] = None

    @classmethod
    def from_event_dict(cls, event: dict):
        event_type = event.get("event_type")
        # We used `user_login` in the past.
        actor = event.get("user_login") or event.get("actor")
        trigger_id = event.get("trigger_id")
        project_url = event.get("project_url")
        tag_name = event.get("tag_name")
        git_ref = event.get("git_ref")
        # event has _pr_id as the attribute while pr_id is a getter property
        pr_id = event.get("_pr_id") or event.get("pr_id")
        commit_sha = event.get("commit_sha")
        identifier = event.get("identifier")
        issue_id = event.get("issue_id")
        task_accepted_time = (
            datetime.fromtimestamp(event.get("task_accepted_time"), timezone.utc)
            if event.get("task_accepted_time")
            else None
        )
        build_targets_override = event.get("build_targets_override")
        tests_targets_override = event.get("tests_targets_override")
        branches_override = event.get("branches_override")

        return EventData(
            event_type=event_type,
            actor=actor,
            trigger_id=trigger_id,
            project_url=project_url,
            tag_name=tag_name,
            git_ref=git_ref,
            pr_id=pr_id,
            commit_sha=commit_sha,
            identifier=identifier,
            event_dict=event,
            issue_id=issue_id,
            task_accepted_time=task_accepted_time,
            build_targets_override=build_targets_override,
            tests_targets_override=tests_targets_override,
            branches_override=branches_override,
        )

    @property
    def project(self) -> Optional[GitProject]:
        if not self._project:
            self._project = self.get_project()
        return self._project

    @property
    def repo_namespace(self) -> str:
        if not self._repo_namespace:
            self._repo_namespace = self.project.namespace

        return self._repo_namespace

    @property
    def repo_name(self) -> str:
        if not self._repo_name:
            self._repo_name = self.project.repo

        return self._repo_name

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            # if new event is created and db_trigger needed - add Add*DbTrigger mixin to your class
            self._db_trigger = self._recreate_original_db_trigger()

        return self._db_trigger

    def _get_db_trigger_class(self) -> Optional[AbstractAddDbTrigger]:
        if not hasattr(packit_service.worker.events, self.event_type):
            logger.warning(
                f"We don't know, what to search in the database for this event type:"
                f" {self.event_type}"
            )
            return None

        logger.debug(f"Trying to get event type class: {self.event_type}")
        # when creating a new event - just add a class to packit_service.worker.events.__init__
        event_cls = getattr(packit_service.worker.events, self.event_type)

        # first object in list is class itself and last is `object` - let's skip it
        for superclass in event_cls.mro()[1:-1]:
            if _AddDbTrigger in superclass.__bases__:
                logger.debug(
                    f"AddDbTrigger class found: {superclass.__name__} in {event_cls.mro()}"
                )
                return superclass

        logger.debug(f"No AddDbTrigger class found in {event_cls.mro()}")
        return None

    def _recreate_arguments_of_get_or_create_staticmethod(
        self, db_trigger_cls: AbstractAddDbTrigger
    ) -> Dict[str, Any]:
        result = {}
        args_of_get_or_create = inspect.getfullargspec(
            db_trigger_cls.get_or_create
        ).args
        for arg in args_of_get_or_create:
            result[arg] = getattr(self, arg)

        return result

    def _recreate_original_db_trigger(self) -> Optional[AbstractTriggerDbType]:
        db_trigger_cls = self._get_db_trigger_class()
        if db_trigger_cls is None:
            return None

        arguments_of_get_or_create_staticmethod = (
            self._recreate_arguments_of_get_or_create_staticmethod(db_trigger_cls)
        )

        logger.debug(
            f"Staticmethod `get_or_create` created for AddDbTrigger class: "
            f"{db_trigger_cls.__class__.__name__} "
            f"with arguments: {arguments_of_get_or_create_staticmethod}"
        )
        return db_trigger_cls.get_or_create(**arguments_of_get_or_create_staticmethod)

    def get_dict(self) -> dict:
        d = self.__dict__
        d = copy.deepcopy(d)
        task_accepted_time = d.get("task_accepted_time")
        d["task_accepted_time"] = (
            int(task_accepted_time.timestamp()) if task_accepted_time else None
        )
        if self.build_targets_override:
            d["build_targets_override"] = list(self.build_targets_override)
        if self.tests_targets_override:
            d["tests_targets_override"] = list(self.tests_targets_override)
        if self.branches_override:
            d["branches_override"] = list(self.branches_override)
        d.pop("_project", None)
        d.pop("_db_trigger", None)
        return d

    def get_project(self) -> Optional[GitProject]:
        if not self.project_url:
            return None
        return ServiceConfig.get_service_config().get_project(
            url=self.project_url or self.db_trigger.project.project_url
        )


class Event:
    task_accepted_time: Optional[datetime] = None
    actor: Optional[str]

    def __init__(self, created_at: Union[int, float, str] = None):
        self.created_at: datetime
        if created_at:
            if isinstance(created_at, (int, float)):
                self.created_at = datetime.fromtimestamp(created_at, timezone.utc)
            elif isinstance(created_at, str):
                # https://stackoverflow.com/questions/127803/how-do-i-parse-an-iso-8601-formatted-date/49784038
                created_at = created_at.replace("Z", "+00:00")
                self.created_at = datetime.fromisoformat(created_at)
        else:
            self.created_at = datetime.now(timezone.utc)

        # lazy properties:
        self._db_trigger: Optional[AbstractTriggerDbType] = None

    @staticmethod
    def ts2str(event: dict):
        """
        Convert 'created_at' key from timestamp to iso 8601 time format.
        This would normally be in a from_dict(), but we don't have such method.
        In api/* we read events from db and directly serve them to clients.
        Deserialize (from_dict) and serialize (to_dict) every entry
        just to do this ts2str would be waste of resources.
        """
        created_at = event.get("created_at")
        if isinstance(created_at, int):
            event["created_at"] = datetime.fromtimestamp(created_at).isoformat()
        return event

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        d = default_dict or self.__dict__
        d = copy.deepcopy(d)
        # whole dict has to be JSON serializable because of redis
        d["event_type"] = self.__class__.__name__

        # we are trying to be lazy => don't touch database if it is not needed
        d["trigger_id"] = self._db_trigger.id if self._db_trigger else None
        # we don't want to save non-serializable object
        d.pop("_db_trigger")

        d["created_at"] = int(d["created_at"].timestamp())
        task_accepted_time = d.get("task_accepted_time")
        d["task_accepted_time"] = (
            int(task_accepted_time.timestamp()) if task_accepted_time else None
        )
        d["project_url"] = d.get("project_url") or (
            self.db_trigger.project.project_url if self.db_trigger else None
        )
        if self.build_targets_override:
            d["build_targets_override"] = list(self.build_targets_override)
        if self.tests_targets_override:
            d["tests_targets_override"] = list(self.tests_targets_override)
        if self.branches_override:
            d["branches_override"] = list(self.branches_override)
        return d

    def get_db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return None

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            self._db_trigger = self.get_db_trigger()
        return self._db_trigger

    @property
    def job_config_trigger_type(self) -> Optional[JobConfigTriggerType]:
        """
        By default, we can use a database model related to this to get the config trigger type.

        Set this for an event subclass if it is clear and
        can be determined without any database connections
        by using a `@use_for_job_config_trigger` decorator.
        """
        for (
            event_cls,
            job_config_trigger_type,
        ) in MAP_EVENT_TO_JOB_CONFIG_TRIGGER_TYPE.items():
            if isinstance(self, event_cls):
                return job_config_trigger_type
        if not self.db_trigger:
            logger.warning(
                f"Event {self} does not have a matching object in the database."
            )
            return None
        return self.db_trigger.job_config_trigger_type

    @property
    def project(self):
        raise NotImplementedError("Please implement me!")

    @property
    def base_project(self):
        raise NotImplementedError("Please implement me!")

    @property
    def package_config(self):
        raise NotImplementedError("Please implement me!")

    @property
    def build_targets_override(self) -> Optional[Set[str]]:
        """
        Return the targets to use for building of the all targets from config
        for the relevant events (e.g.rerunning of a single check).
        """
        return None

    @property
    def tests_targets_override(self) -> Optional[Set[str]]:
        """
        Return the targets to use for testing of the all targets from config
        for the relevant events (e.g.rerunning of a single check).
        """
        return None

    @property
    def branches_override(self) -> Optional[Set[str]]:
        """
        Return the branches to use for propose-downstream of the all branches from config
        for the relevant events (e.g.rerunning of a single check).
        """
        return None

    def get_package_config(self):
        raise NotImplementedError("Please implement me!")

    def get_project(self) -> GitProject:
        raise NotImplementedError("Please implement me!")

    def pre_check(self) -> bool:
        """
        Implement this method for those events, where you want to check if event properties are
        correct. If this method returns False during runtime, execution of service code is skipped.

        :return: False if we can ignore the event
        """
        return True

    def __str__(self):
        return str(self.get_dict())

    def __repr__(self):
        return f"{self.__class__.__name__}({self.get_dict()})"


class AbstractForgeIndependentEvent(Event):
    commit_sha: Optional[str]
    project_url: str

    def __init__(
        self,
        created_at: Union[int, float, str] = None,
        project_url=None,
        pr_id: Optional[int] = None,
        actor: Optional[str] = None,
    ):
        super().__init__(created_at)
        self.project_url = project_url
        self._pr_id = pr_id
        self.fail_when_config_file_missing = False
        self.actor = actor

        # Lazy properties
        self._project: Optional[GitProject] = None
        self._base_project: Optional[GitProject] = None
        self._package_config: Optional[PackageConfig] = None
        self._package_config_searched: bool = False

    @property
    def project(self):
        if not self._project:
            self._project = self.get_project()
        return self._project

    @property
    def base_project(self):
        if not self._base_project:
            self._base_project = self.get_base_project()
        return self._base_project

    @property
    def package_config(self):
        if not self._package_config_searched and not self._package_config:
            self._package_config = self.get_package_config()
            self._package_config_searched = True
        return self._package_config

    def get_db_trigger(self) -> Optional[AbstractTriggerDbType]:
        raise NotImplementedError()

    @property
    def pr_id(self) -> Optional[int]:
        return self._pr_id

    def get_project(self) -> Optional[GitProject]:
        if not (self.project_url or self.db_trigger):
            return None

        return ServiceConfig.get_service_config().get_project(
            url=self.project_url or self.db_trigger.project.project_url
        )

    def get_base_project(self) -> Optional[GitProject]:
        """Reimplement in the PR events."""
        return None

    def get_package_config(self) -> Optional[PackageConfig]:
        logger.debug(
            f"Getting package_config:\n"
            f"\tproject: {self.project}\n"
            f"\tbase_project: {self.base_project}\n"
            f"\treference: {self.commit_sha}\n"
            f"\tpr_id: {self.pr_id}"
        )

        package_config = PackageConfigGetter.get_package_config_from_repo(
            base_project=self.base_project,
            project=self.project,
            reference=self.commit_sha,
            pr_id=self.pr_id,
            fail_when_missing=self.fail_when_config_file_missing,
        )

        # job config change note:
        #   this is used in sync-from-downstream which is buggy - we don't need to change this
        if package_config:
            package_config.upstream_project_url = self.project_url
        return package_config

    @staticmethod
    def _get_submitted_time_from_model(
        model: Union[CoprBuildTargetModel, TFTTestRunTargetModel]
    ) -> datetime:
        # TODO: unify `submitted_name` (or better -> create for both models `task_accepted_time`)
        # to delete this mess plz
        if isinstance(model, CoprBuildTargetModel):
            return model.build_submitted_time

        return model.submitted_time

    @classmethod
    def get_most_recent_targets(
        cls,
        models: Union[
            Optional[Iterable[CoprBuildTargetModel]],
            Optional[Iterable[TFTTestRunTargetModel]],
        ],
    ) -> List[Union[CoprBuildTargetModel, TFTTestRunTargetModel]]:
        """
        Gets most recent models from an iterable (regarding submission time).

        Args:
            models: Copr or TF models - if there are any duplicates in them then use the most
             recent model

        Returns:
            Dictionary - target as a key and corresponding most recent model as a value.
        """
        most_recent_models: Dict[
            str, Union[CoprBuildTargetModel, TFTTestRunTargetModel]
        ] = {}
        for model in models:
            submitted_time_of_current_model = cls._get_submitted_time_from_model(model)
            if (
                most_recent_models.get(model.target) is None
                or cls._get_submitted_time_from_model(most_recent_models[model.target])
                < submitted_time_of_current_model
            ):
                most_recent_models[model.target] = model

        return list(most_recent_models.values())

    @classmethod
    def _filter_most_recent_models_targets_by_status(
        cls,
        models: Union[
            Optional[Iterable[CoprBuildTargetModel]],
            Optional[Iterable[TFTTestRunTargetModel]],
        ],
        statuses_to_filter_with: List[str],
    ) -> Optional[Set[str]]:
        logger.info(
            f"Trying to filter targets with possible status: {statuses_to_filter_with} in {models}"
        )
        failed_models_targets = set()
        for model in cls.get_most_recent_targets(models):
            if model.status in statuses_to_filter_with:
                failed_models_targets.add(model.target)

        logger.info(f"Targets found: {failed_models_targets}")
        return failed_models_targets if failed_models_targets else None

    def get_all_tf_targets_by_status(
        self, statuses_to_filter_with: List[str]
    ) -> Optional[Set[str]]:
        if self.commit_sha is None:
            return None

        logger.debug(
            f"Getting failed Testing Farm targets for commit sha: {self.commit_sha}"
        )
        return self._filter_most_recent_models_targets_by_status(
            models=TFTTestRunTargetModel.get_all_by_commit_target(
                commit_sha=self.commit_sha
            ),
            statuses_to_filter_with=statuses_to_filter_with,
        )

    def get_all_build_targets_by_status(
        self, statuses_to_filter_with: List[str]
    ) -> Optional[Set[str]]:
        if self.commit_sha is None or self.project.repo is None:
            return None

        logger.debug(
            f"Getting failed COPR build targets for commit sha: {self.commit_sha}"
        )
        return self._filter_most_recent_models_targets_by_status(
            models=CoprBuildTargetModel.get_all_by_commit(commit_sha=self.commit_sha),
            statuses_to_filter_with=statuses_to_filter_with,
        )

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        # so that it is JSON serializable (because of Celery tasks)
        result.pop("_project")
        result.pop("_base_project")
        result.pop("_package_config")
        return result
