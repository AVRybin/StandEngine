#!/bin/sh
set -eu

PULUMI_HOME="${PULUMI_HOME%/}/$(id -u)"
export PULUMI_HOME

mkdir -p "${PULUMI_HOME}/plugins"

if [ -d /opt/pulumi/plugins ]; then
    for plugin in /opt/pulumi/plugins/*; do
        [ -e "${plugin}" ] || continue
        destination="${PULUMI_HOME}/plugins/$(basename "${plugin}")"
        if [ ! -e "${destination}" ]; then
            cp -R "${plugin}" "${destination}"
        fi
    done
fi

exec stands-engine "$@"
