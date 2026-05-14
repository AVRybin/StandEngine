from dataclasses import dataclass, field
from urllib.parse import urlencode
from typing import Optional
from pulumi import automation as auto
from typing import Callable

from pulumi.automation import UpResult, DestroyResult, PreviewResult


@dataclass(kw_only=True)
class MetalProvision:
    s3_bucket: str
    s3_region: str
    s3_endpoint: str

    s3_access_key: str
    s3_secret_key: str

    stand_name: str
    project_name: str
    user_name: str
    passphrase: str

    provider_token: str

    stack: Optional[auto.Stack] = field(init=False, default=None)

    def build_s3_url(self) -> str:
        endpoint = self.s3_endpoint
        endpoint = endpoint.removeprefix("https://").removeprefix("http://")

        query = {
            "region": self.s3_region,
            "endpoint": endpoint,
            "s3ForcePathStyle": "true",
        }

        prefix = self.user_name.strip("/")

        if prefix:
            backend_base_url = f"s3://{self.s3_bucket}/{prefix}"
        else:
            backend_base_url = f"s3://{self.s3_bucket}"

        return f"{backend_base_url}?{urlencode(query)}"

    @staticmethod
    def resource_name_from_urn(urn: str | None) -> str:
        if not urn:
            return "<unknown>"

        return urn.split("::")[-1]

    IGNORED_RESOURCE_TYPES = frozenset({
        "pulumi:pulumi:Stack",
        "pulumi:providers:hcloud",
    })

    def _log_resource_event(self, metadata, prefix: str = "", respect_ignored: bool = True,) -> None:
        resource_type = getattr(metadata, "type", "<type>")
        if respect_ignored and resource_type in self.IGNORED_RESOURCE_TYPES:
            return

        op = getattr(metadata, "op", "<op>")
        urn = getattr(metadata, "urn", None)
        name = self.resource_name_from_urn(urn)
        print(f"[pulumi] {prefix}{op}: {resource_type}::{name}")



    def event_handler(self, event) -> None:
        try:
            if event.resource_pre_event:
                meta = event.resource_pre_event.metadata
                if meta.op != "same":
                    print(f"[DIFF] {meta.urn}")
                    print(f"  op: {meta.op}")
                    print(f"  diffs: {getattr(meta, 'diffs', None)}")
                    print(f"  detailed_diff: {getattr(meta, 'detailed_diff', None)}")
                    print(f"  olds keys: {list(getattr(meta, 'olds', {}).keys())}")
                    print(f"  news keys: {list(getattr(meta, 'news', {}).keys())}")
            if event.diagnostic_event:
                diag = event.diagnostic_event
                severity = getattr(diag, "severity", None)
                message = getattr(diag, "message", "")
                if severity in ("error", "warning"):
                    print(f"[pulumi:{severity}] {message.strip()}")

            if event.resource_pre_event:
                self._log_resource_event(event.resource_pre_event.metadata)

            if event.res_outputs_event:
                self._log_resource_event(event.res_outputs_event.metadata, prefix="done ")

            if event.res_op_failed_event:
                self._log_resource_event(
                    event.res_op_failed_event.metadata,
                    prefix="failed ",
                    respect_ignored=False,
                )

            if event.summary_event:
                changes = getattr(event.summary_event, "resource_changes", None)
                print(f"[pulumi] summary: {changes}")
        except Exception as exc:
            print(f"[pulumi:event-handler-warning] {exc}")

    def init_stack(self, server_program: Callable[[], None]) -> None:
        self.stack = auto.create_or_select_stack(
            stack_name=self.stand_name,
            project_name=self.project_name,
            program=server_program,
            opts=auto.LocalWorkspaceOptions(
                secrets_provider="passphrase",
                project_settings=auto.ProjectSettings(
                    name=self.project_name,
                    runtime="python",
                    backend=auto.ProjectBackend(url=self.build_s3_url()),
                ),
                env_vars={
                    "PULUMI_CONFIG_PASSPHRASE": self.passphrase,
                    "AWS_ACCESS_KEY_ID": self.s3_access_key,
                    "AWS_SECRET_ACCESS_KEY": self.s3_secret_key,
                    "AWS_REGION": self.s3_region,
                },
            ),
        )

        if self.stack is None:
            raise Exception("Stack is None")

        self.stack.set_config(
        "hcloud:token",
            auto.ConfigValue(
                value=self.provider_token,
                secret=True,
            ),
        )


    def create(self, server_program: Callable[[], None]) -> UpResult:
        if self.stack is None:
            self.init_stack(server_program)

        if self.stack is None:
            raise Exception("Stack is None")

        return  self.stack.up(on_event=self.event_handler, color="never", suppress_progress=True,
                            suppress_outputs=True,show_secrets=False)

    def destroy(self, server_program: Callable[[], None]) -> DestroyResult:
        if self.stack is None:
            self.init_stack(server_program)

        if self.stack is None:
            raise Exception("Stack is None")

        return self.stack.destroy(on_event=self.event_handler, color="never", suppress_progress=True,
                             suppress_outputs=True, show_secrets=False)

    def prev(self, server_program: Callable[[], None]) -> PreviewResult:
        if self.stack is None:
            self.init_stack(server_program)

        if self.stack is None:
            raise Exception("Stack is None")

        return self.stack.preview(on_event=self.event_handler, color="never", suppress_progress=True,
                             suppress_outputs=True)