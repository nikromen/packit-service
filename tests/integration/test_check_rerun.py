# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest
from celery.canvas import Signature
from flexmock import flexmock
from github import Github
from ogr.services.github import GithubProject

from packit.config import JobConfigTriggerType
from packit.local_project import LocalProject
from packit_service.constants import PG_BUILD_STATUS_SUCCESS, TASK_ACCEPTED
from packit_service.models import (
    GitBranchModel,
    JobTriggerModel,
    ProjectReleaseModel,
    PullRequestModel,
    JobTriggerModelType,
)
from packit_service.service.db_triggers import (
    AddBranchPushDbTrigger,
    AddPullRequestDbTrigger,
    AddReleaseDbTrigger,
)
from packit_service.worker.handlers import ProposeDownstreamHandler
from packit_service.worker.helpers.build import (
    CoprBuildJobHelper,
    KojiBuildJobHelper,
    copr_build,
)
from packit_service.worker.helpers.propose_downstream import ProposeDownstreamJobHelper
from packit_service.worker.helpers.testing_farm import TestingFarmJobHelper
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.monitoring import Pushgateway
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.reporting import StatusReporterGithubChecks
from packit_service.worker.result import TaskResults
from packit_service.worker.tasks import (
    run_copr_build_handler,
    run_koji_build_handler,
    run_testing_farm_handler,
)
from tests.spellbook import DATA_DIR, first_dict_value, get_parameters_from_results


@pytest.fixture(scope="module")
def check_rerun_event_testing_farm():
    return json.loads(
        (DATA_DIR / "webhooks" / "github" / "checkrun_rerequested.json").read_text()
    )


@pytest.fixture(scope="module")
def check_rerun_event_copr_build():
    event = json.loads(
        (DATA_DIR / "webhooks" / "github" / "checkrun_rerequested.json").read_text()
    )
    event["check_run"]["name"] = "rpm-build:fedora-rawhide-x86_64"
    return event


@pytest.fixture(scope="module")
def check_rerun_event_koji_build():
    event = json.loads(
        (DATA_DIR / "webhooks" / "github" / "checkrun_rerequested.json").read_text()
    )
    event["check_run"]["name"] = "production-build:f34"
    return event


@pytest.fixture(scope="module")
def check_rerun_event_propose_downstream():
    event = json.loads(
        (DATA_DIR / "webhooks" / "github" / "checkrun_rerequested.json").read_text()
    )
    event["check_run"]["name"] = "propose-downstream:f34"
    return event


@pytest.fixture
def mock_pr_functionality(request):
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs':"
        + str(request.param)
        + "}"
    )
    flexmock(
        GithubProject,
        full_repo_name="packit/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        get_pr=lambda pr_id: flexmock(head_commit="12345"),
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.pull_request,
        job_trigger_model_type=JobTriggerModelType.pull_request,
        id=123,
    )
    flexmock(AddPullRequestDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(PullRequestModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(JobTriggerModel).should_receive("get_by_id").with_args(123456).and_return(
        trigger
    )
    flexmock(trigger).should_receive("get_trigger_object").and_return(
        PullRequestModel(pr_id=123)
    )
    flexmock(PullRequestModel).should_receive("get_or_create").with_args(
        pr_id=123,
        namespace="packit",
        repo_name="hello-world",
        project_url="https://github.com/packit/hello-world",
    ).and_return(
        flexmock(id=12, job_config_trigger_type=JobConfigTriggerType.pull_request)
    )
    flexmock(JobTriggerModel).should_receive("get_or_create").and_return(
        flexmock(id=123456)
    )


@pytest.fixture
def mock_push_functionality(request):
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs':"
        + str(request.param)
        + "}"
    )
    flexmock(
        GithubProject,
        full_repo_name="packit/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        get_pr=lambda pr_id: flexmock(head_commit="12345"),
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.commit,
        job_trigger_model_type=JobTriggerModelType.branch_push,
        id=123,
    )
    flexmock(AddBranchPushDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(GitBranchModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(JobTriggerModel).should_receive("get_by_id").with_args(123456).and_return(
        trigger
    )
    flexmock(trigger).should_receive("get_trigger_object").and_return(
        GitBranchModel(name="main")
    )
    flexmock(GitBranchModel).should_receive("get_or_create").with_args(
        branch_name="main",
        namespace="packit",
        repo_name="hello-world",
        project_url="https://github.com/packit/hello-world",
    ).and_return(flexmock(id=12, job_config_trigger_type=JobConfigTriggerType.commit))
    flexmock(JobTriggerModel).should_receive("get_or_create").and_return(
        flexmock(id=123456)
    )


@pytest.fixture
def mock_release_functionality(request):
    packit_yaml = (
        "{'specfile_path': 'the-specfile.spec', 'synced_files': [], 'jobs':"
        + str(request.param)
        + "}"
    )
    flexmock(
        GithubProject,
        full_repo_name="packit/hello-world",
        get_file_content=lambda path, ref: packit_yaml,
        get_files=lambda ref, filter_regex: ["the-specfile.spec"],
        get_web_url=lambda: "https://github.com/the-namespace/the-repo",
        get_pr=lambda pr_id: flexmock(head_commit="12345"),
    )
    flexmock(Github, get_repo=lambda full_name_or_id: None)

    trigger = flexmock(
        job_config_trigger_type=JobConfigTriggerType.release,
        job_trigger_model_type=JobTriggerModelType.release,
        id=123,
    )
    flexmock(AddReleaseDbTrigger).should_receive("db_trigger").and_return(trigger)
    flexmock(ProjectReleaseModel).should_receive("get_by_id").with_args(123).and_return(
        trigger
    )
    flexmock(LocalProject, refresh_the_arguments=lambda: None)
    flexmock(JobTriggerModel).should_receive("get_by_id").with_args(123456).and_return(
        trigger
    )
    flexmock(trigger).should_receive("get_trigger_object").and_return(
        ProjectReleaseModel(tag_name="0.1.0")
    )
    flexmock(ProjectReleaseModel).should_receive("get_or_create").with_args(
        tag_name="0.1.0",
        namespace="packit",
        repo_name="hello-world",
        project_url="https://github.com/packit/hello-world",
        commit_hash="0e5d8b51fd5dfa460605e1497d22a76d65c6d7fd",
    ).and_return(flexmock(id=12, job_config_trigger_type=JobConfigTriggerType.release))
    flexmock(JobTriggerModel).should_receive("get_or_create").and_return(
        flexmock(id=123456)
    )


@pytest.mark.parametrize(
    "mock_pr_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-all"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_check_rerun_pr_copr_build_handler(
    mock_pr_functionality, check_rerun_event_copr_build
):
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    ).once()
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-34-x86_64"}
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="rpm-build:fedora-rawhide-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_copr_build)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)
    assert event_dict["build_targets_override"] == ["fedora-rawhide-x86_64"]

    results = run_copr_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_pr_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "tests",
                    "metadata": {"targets": "fedora-all"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_check_rerun_pr_testing_farm_handler(
    mock_pr_functionality, check_rerun_event_testing_farm
):

    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=True, details={})
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        flexmock(status=PG_BUILD_STATUS_SUCCESS)
    )
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-34-x86_64"}
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="testing-farm:fedora-rawhide-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_testing_farm)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)
    assert event_dict["tests_targets_override"] == ["fedora-rawhide-x86_64"]
    results = run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_pr_functionality",
    (
        [
            [
                {
                    "trigger": "pull_request",
                    "job": "production_build",
                    "metadata": {"targets": "fedora-all", "scratch": "true"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_check_rerun_pr_koji_build_handler(
    mock_pr_functionality, check_rerun_event_koji_build
):
    flexmock(KojiBuildJobHelper).should_receive("run_koji_build").and_return(
        TaskResults(success=True, details={})
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-34-x86_64"}
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="production-build:f34",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_koji_build)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)
    assert event_dict["build_targets_override"] == ["f34"]

    results = run_koji_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_push_functionality",
    (
        [
            [
                {
                    "trigger": "commit",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-all"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_check_rerun_push_copr_build_handler(
    mock_push_functionality, check_rerun_event_copr_build
):
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    ).once()
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-34-x86_64"}
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="rpm-build:fedora-rawhide-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_copr_build)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert json.dumps(event_dict)
    assert event_dict["build_targets_override"] == ["fedora-rawhide-x86_64"]

    results = run_copr_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_push_functionality",
    (
        [
            [
                {
                    "trigger": "commit",
                    "job": "tests",
                    "targets": [
                        "fedora-all",
                    ],
                }
            ]
        ]
    ),
    indirect=True,
)
def test_check_rerun_push_testing_farm_handler(
    mock_push_functionality, check_rerun_event_testing_farm
):

    flexmock(TestingFarmJobHelper).should_receive("run_testing_farm").once().and_return(
        TaskResults(success=True, details={})
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(TestingFarmJobHelper).should_receive("get_latest_copr_build").and_return(
        flexmock(status=PG_BUILD_STATUS_SUCCESS)
    )
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-34-x86_64"}
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="testing-farm:fedora-rawhide-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_testing_farm)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert event_dict["tests_targets_override"] == ["fedora-rawhide-x86_64"]
    assert json.dumps(event_dict)
    results = run_testing_farm_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_push_functionality",
    (
        [
            [
                {
                    "trigger": "commit",
                    "job": "production_build",
                    "metadata": {"targets": "fedora-all", "scratch": "true"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_check_rerun_push_koji_build_handler(
    mock_push_functionality, check_rerun_event_koji_build
):
    flexmock(KojiBuildJobHelper).should_receive("run_koji_build").and_return(
        TaskResults(success=True, details={})
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-34-x86_64"}
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="production-build:f34",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_koji_build)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert event_dict["build_targets_override"] == ["f34"]
    assert json.dumps(event_dict)

    results = run_koji_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_release_functionality",
    (
        [
            [
                {
                    "trigger": "release",
                    "job": "copr_build",
                    "metadata": {"targets": "fedora-all"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_check_rerun_release_copr_build_handler(
    mock_release_functionality, check_rerun_event_copr_build
):
    flexmock(CoprBuildJobHelper).should_receive("run_copr_build").and_return(
        TaskResults(success=True, details={})
    ).once()
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-34-x86_64"}
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="rpm-build:fedora-rawhide-x86_64",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_copr_build)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert event_dict["build_targets_override"] == ["fedora-rawhide-x86_64"]
    assert json.dumps(event_dict)

    results = run_copr_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_release_functionality",
    (
        [
            [
                {
                    "trigger": "release",
                    "job": "production_build",
                    "metadata": {"targets": "fedora-all", "scratch": "true"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_check_rerun_release_koji_build_handler(
    mock_release_functionality, check_rerun_event_koji_build
):
    flexmock(KojiBuildJobHelper).should_receive("run_koji_build").and_return(
        TaskResults(success=True, details={})
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-34-x86_64"}
    )
    flexmock(StatusReporterGithubChecks).should_receive("set_status").with_args(
        state=BaseCommitStatus.pending,
        description=TASK_ACCEPTED,
        check_name="production-build:f34",
        url="",
        links_to_external_services=None,
        markdown_content=None,
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").twice().and_return()

    processing_results = SteveJobs().process_message(check_rerun_event_koji_build)
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert event_dict["build_targets_override"] == ["f34"]
    assert json.dumps(event_dict)

    results = run_koji_build_handler(
        package_config=package_config,
        event=event_dict,
        job_config=job_config,
    )
    assert first_dict_value(results["job"])["success"]


@pytest.mark.parametrize(
    "mock_release_functionality",
    (
        [
            [
                {
                    "trigger": "release",
                    "job": "propose_downstream",
                    "metadata": {"targets": "fedora-all"},
                }
            ]
        ]
    ),
    indirect=True,
)
def test_check_rerun_release_propose_downstream_handler(
    mock_release_functionality, check_rerun_event_propose_downstream
):
    flexmock(ProposeDownstreamHandler).should_receive("run").and_return(
        TaskResults(success=True, details={})
    )
    flexmock(GithubProject).should_receive("get_files").and_return(["foo.spec"])
    flexmock(GithubProject).should_receive("get_web_url").and_return(
        "https://github.com/the-namespace/the-repo"
    )
    flexmock(GithubProject).should_receive("is_private").and_return(False)
    flexmock(copr_build).should_receive("get_valid_build_targets").and_return(
        {"fedora-rawhide-x86_64", "fedora-34-x86_64"}
    )
    flexmock(ProposeDownstreamJobHelper).should_receive(
        "report_status_to_all"
    ).with_args(
        description=TASK_ACCEPTED,
        state=BaseCommitStatus.pending,
        url="",
    ).once()
    flexmock(Signature).should_receive("apply_async").once()
    flexmock(Pushgateway).should_receive("push").once().and_return()

    processing_results = SteveJobs().process_message(
        check_rerun_event_propose_downstream
    )
    event_dict, job, job_config, package_config = get_parameters_from_results(
        processing_results
    )
    assert event_dict["branches_override"] == ["f34"]
    assert json.dumps(event_dict)
