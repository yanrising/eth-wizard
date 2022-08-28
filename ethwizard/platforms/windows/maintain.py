import subprocess
import httpx
import re
import time
import os
import shlex

from pathlib import Path

from urllib.parse import urljoin

from defusedxml import ElementTree

from dateutil.parser import parse as dateparse

from zipfile import ZipFile

from packaging.version import parse as parse_version, Version

from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.shortcuts import button_dialog

from ethwizard.platforms.common import (
    select_fee_recipient_address,
    get_geth_running_version,
    get_geth_latest_version
)

from ethwizard.platforms.windows.common import (
    save_state,
    log,
    quit_app,
    get_service_details,
    get_nssm_binary,
    is_stable_windows_amd64_archive,
    install_gpg
)

from ethwizard.constants import (
    CTX_SELECTED_EXECUTION_CLIENT,
    CTX_SELECTED_CONSENSUS_CLIENT,
    CTX_SELECTED_NETWORK,
    CTX_SELECTED_DIRECTORY,
    EXECUTION_CLIENT_GETH,
    CONSENSUS_CLIENT_TEKU,
    WIZARD_COMPLETED_STEP_ID,
    UNKNOWN_VALUE,
    MAINTENANCE_DO_NOTHING,
    MIN_CLIENT_VERSION_FOR_MERGE,
    MAINTENANCE_START_SERVICE,
    MAINTENANCE_RESTART_SERVICE,
    MAINTENANCE_CONFIG_CLIENT_MERGE,
    MAINTENANCE_UPGRADE_CLIENT,
    MAINTENANCE_UPGRADE_CLIENT_MERGE,
    MAINTENANCE_REINSTALL_CLIENT,
    WINDOWS_SERVICE_RUNNING,
    BN_VERSION_EP,
    GITHUB_REST_API_URL,
    GITHUB_API_VERSION,
    TEKU_LATEST_RELEASE,
    GETH_STORE_BUILDS_PARAMS,
    GETH_STORE_BUILDS_URL,
    GETH_BUILDS_BASE_URL,
    PGP_KEY_SERVERS,
    GETH_WINDOWS_PGP_KEY_ID
)

def enter_maintenance(context):
    # Maintenance entry point for Windows.
    # Maintenance is started after the wizard has completed.

    log.info(f'Entering maintenance mode. To be implemented.')

    if context is None:
        log.error('Missing context.')

    context = use_default_client(context)

    if context is None:
        log.error('Missing context.')

    return show_dashboard(context)

def show_dashboard(context):
    # Show simple dashboard

    selected_execution_client = CTX_SELECTED_EXECUTION_CLIENT
    selected_consensus_client = CTX_SELECTED_CONSENSUS_CLIENT
    selected_network = CTX_SELECTED_NETWORK
    selected_directory = CTX_SELECTED_DIRECTORY

    current_execution_client = context[selected_execution_client]
    current_consensus_client = context[selected_consensus_client]
    current_network = context[selected_network]
    current_directory = context[selected_directory]

    # Get execution client details

    execution_client_details = get_execution_client_details(current_directory,
        current_execution_client)
    if not execution_client_details:
        log.error('Unable to get execution client details.')
        return False

    # Find out if we need to do maintenance for the execution client

    execution_client_details['next_step'] = MAINTENANCE_DO_NOTHING

    installed_version = execution_client_details['versions']['installed']
    if installed_version != UNKNOWN_VALUE:
        installed_version = parse_version(installed_version)
    running_version = execution_client_details['versions']['running']
    if running_version != UNKNOWN_VALUE:
        running_version = parse_version(running_version)
    latest_version = execution_client_details['versions']['latest']
    if latest_version != UNKNOWN_VALUE:
        latest_version = parse_version(latest_version)
    
    # Merge tests for execution client
    merge_ready_exec_version = parse_version(
        MIN_CLIENT_VERSION_FOR_MERGE[current_network][current_execution_client])

    is_installed_exec_merge_ready = False
    if is_version(installed_version) and is_version(merge_ready_exec_version):
        if installed_version >= merge_ready_exec_version:
            is_installed_exec_merge_ready = True

    is_latest_exec_merge_ready = False
    if is_version(latest_version) and is_version(merge_ready_exec_version):
        if latest_version >= merge_ready_exec_version:
            is_latest_exec_merge_ready = True

    # If the service is not running, we need to start it

    if not execution_client_details['service']['running']:
        execution_client_details['next_step'] = MAINTENANCE_START_SERVICE

    # If the running version is older than the installed one, we need to restart the service

    if is_version(installed_version) and is_version(running_version):
        if running_version < installed_version:
            execution_client_details['next_step'] = MAINTENANCE_RESTART_SERVICE

    # If the installed version is merge ready but the client is not configured for the merge,
    # we need to configure the client for the merge

    if is_version(installed_version):
        if is_installed_exec_merge_ready and not execution_client_details['is_merge_configured']:
            execution_client_details['next_step'] = MAINTENANCE_CONFIG_CLIENT_MERGE

    # If the installed version is older than the available one, we need to upgrade the client

    if is_version(installed_version) and is_version(latest_version):
        if installed_version < latest_version:
            execution_client_details['next_step'] = MAINTENANCE_UPGRADE_CLIENT
        
        # If the next version is merge ready and we are not configured yet, we need to upgrade and
        # configure the client

        if is_latest_exec_merge_ready and not execution_client_details['is_merge_configured']:
            execution_client_details['next_step'] = MAINTENANCE_UPGRADE_CLIENT_MERGE

    # If the service is not installed or found, we need to reinstall the client

    if not execution_client_details['service']['found']:
        execution_client_details['next_step'] = MAINTENANCE_REINSTALL_CLIENT

    # Get consensus client details

    consensus_client_details = get_consensus_client_details(current_directory,
        current_consensus_client)
    if not consensus_client_details:
        log.error('Unable to get consensus client details.')
        return False

    # Find out if we need to do maintenance for the consensus client

    consensus_client_details['next_step'] = MAINTENANCE_DO_NOTHING

    installed_version = consensus_client_details['versions']['installed']
    if installed_version != UNKNOWN_VALUE:
        installed_version = parse_version(installed_version)
    running_version = consensus_client_details['versions']['running']
    if running_version != UNKNOWN_VALUE:
        running_version = parse_version(running_version)
    latest_version = consensus_client_details['versions']['latest']
    if latest_version != UNKNOWN_VALUE:
        latest_version = parse_version(latest_version)
    
    # Merge tests for consensus client
    merge_ready_cons_version = parse_version(
        MIN_CLIENT_VERSION_FOR_MERGE[current_network][current_consensus_client])

    is_installed_cons_merge_ready = False
    if is_version(installed_version) and is_version(merge_ready_cons_version):
        if installed_version >= merge_ready_cons_version:
            is_installed_cons_merge_ready = True

    is_latest_cons_merge_ready = False
    if is_version(latest_version) and is_version(merge_ready_cons_version):
        if latest_version >= merge_ready_cons_version:
            is_latest_cons_merge_ready = True

    # If the service is not running, we need to start it

    if not consensus_client_details['bn_service']['running']:
        consensus_client_details['next_step'] = MAINTENANCE_START_SERVICE

    if not consensus_client_details['vc_service']['running']:
        consensus_client_details['next_step'] = MAINTENANCE_START_SERVICE

    # If the running version is older than the installed one, we need to restart the services

    if is_version(installed_version) and is_version(running_version):
        if running_version < installed_version:
            consensus_client_details['next_step'] = MAINTENANCE_RESTART_SERVICE

    # If the installed version is merge ready but the client is not configured for the merge,
    # we need to configure the client for the merge

    if is_version(installed_version):
        if is_installed_cons_merge_ready and (
            not consensus_client_details['is_bn_merge_configured'] or
            not consensus_client_details['is_vc_merge_configured']):
            consensus_client_details['next_step'] = MAINTENANCE_CONFIG_CLIENT_MERGE

    # If the installed version is older than the latest one, we need to upgrade the client

    if is_version(installed_version) and is_version(latest_version):
        if installed_version < latest_version:
            consensus_client_details['next_step'] = MAINTENANCE_UPGRADE_CLIENT
        
        # If the next version is merge ready and we are not configured yet, we need to upgrade and
        # configure the client

        if is_latest_cons_merge_ready and (
            not consensus_client_details['is_bn_merge_configured'] or
            not consensus_client_details['is_vc_merge_configured']):
            consensus_client_details['next_step'] = MAINTENANCE_UPGRADE_CLIENT_MERGE

    # If the service is not installed or found, we need to reinstall the client

    if (not consensus_client_details['bn_service']['found'] or
        not consensus_client_details['vc_service']['found']):
        consensus_client_details['next_step'] = MAINTENANCE_REINSTALL_CLIENT

    # We only need to do maintenance if either the execution or the consensus client needs
    # maintenance.

    maintenance_needed = (
        execution_client_details['next_step'] != MAINTENANCE_DO_NOTHING or
        consensus_client_details['next_step'] != MAINTENANCE_DO_NOTHING)

    # Build the dashboard with the details we have

    maintenance_tasks_description = {
        MAINTENANCE_DO_NOTHING: 'Nothing to perform here. Everything is good.',
        MAINTENANCE_RESTART_SERVICE: 'Service needs to be restarted.',
        MAINTENANCE_UPGRADE_CLIENT: 'Client needs to be upgraded.',
        MAINTENANCE_UPGRADE_CLIENT_MERGE: (
            'Client needs to be upgraded and configured for the merge.'),
        MAINTENANCE_CONFIG_CLIENT_MERGE: 'Client needs to be configured for the merge.',
        MAINTENANCE_START_SERVICE: 'Service needs to be started.',
        MAINTENANCE_REINSTALL_CLIENT: 'Client needs to be reinstalled.',
    }

    buttons = [
        ('Quit', False),
    ]

    maintenance_message = 'Nothing is needed in terms of maintenance.'

    if maintenance_needed:
        buttons = [
            ('Maintain', 1),
            ('Quit', False),
        ]

        maintenance_message = 'Some maintenance tasks are pending. Select maintain to perform them.'

    ec_section = (f'<b>Geth</b> details (I: {execution_client_details["versions"]["installed"]}, '
        f'R: {execution_client_details["versions"]["running"]}, '
        f'L: {execution_client_details["versions"]["latest"]})\n'
        f'Service is running: {execution_client_details["service"]["running"]}\n'
        f'<b>Maintenance task</b>: {maintenance_tasks_description.get(execution_client_details["next_step"], UNKNOWN_VALUE)}')

    cc_services = f'Running services - Beacon node: {consensus_client_details["bn_service"]["running"]}, Validator client: {consensus_client_details["vc_service"]["running"]}\n'
    if consensus_client_details['unified_service']:
        cc_services = f'Service is running: {consensus_client_details["bn_service"]["running"]}\n'

    cc_section = (f'<b>Teku</b> details (I: {consensus_client_details["versions"]["installed"]}, '
        f'R: {consensus_client_details["versions"]["running"]}, '
        f'L: {consensus_client_details["versions"]["latest"]})\n'
        f'{cc_services}'
        f'<b>Maintenance task</b>: {maintenance_tasks_description.get(consensus_client_details["next_step"], UNKNOWN_VALUE)}')

    result = button_dialog(
        title='Maintenance Dashboard',
        text=(HTML(
f'''
Here are some details about your Ethereum clients.

{ec_section}

{cc_section}

{maintenance_message}

Versions legend - I: Installed, R: Running, L: Latest
'''             )),
        buttons=buttons
    ).run()

    if not result:
        return False
    
    if result == 1:
        if perform_maintenance(current_directory, current_execution_client,
            execution_client_details, current_consensus_client, consensus_client_details):
            return show_dashboard(context)
        else:
            log.error('We could not perform all the maintenance tasks.')
            return False

def is_version(value):
    # Return true if this is a packaging version
    return isinstance(value, Version)

def is_service_running(service_details):
    # Return true if this Windows service is running
    return service_details['status'] == WINDOWS_SERVICE_RUNNING

def get_execution_client_details(base_directory, execution_client):
    # Get the details for the current execution client

    base_directory = Path(base_directory)

    nssm_binary = get_nssm_binary()
    if not nssm_binary:
        return False

    if execution_client == EXECUTION_CLIENT_GETH:

        details = {
            'service': {
                'found': False,
                'status': UNKNOWN_VALUE,
                'binary': UNKNOWN_VALUE,
                'parameters': UNKNOWN_VALUE,
                'running': UNKNOWN_VALUE
            },
            'versions': {
                'installed': UNKNOWN_VALUE,
                'running': UNKNOWN_VALUE,
                'latest': UNKNOWN_VALUE
            },
            'exec': {
                'path': UNKNOWN_VALUE,
                'argv': []
            },
            'is_merge_configured': UNKNOWN_VALUE
        }
        
        # Check for existing service
        geth_service_exists = False
        geth_service_name = 'geth'

        service_details = get_service_details(nssm_binary, geth_service_name)

        if service_details is not None:
            geth_service_exists = True
        
        if not geth_service_exists:
            return details

        details['service']['found'] = True
        details['service']['status'] = service_details['status']
        details['service']['binary'] = service_details['install']
        details['service']['parameters'] = service_details['parameters']['AppParameters']
        details['service']['running'] = is_service_running(service_details)

        details['versions']['installed'] = get_geth_installed_version(base_directory)
        details['versions']['running'] = get_geth_running_version(log)
        details['versions']['latest'] = get_geth_latest_version(log)

        details['exec']['path'] = service_details['install']
        details['exec']['argv'] = shlex.split(service_details['parameters']['AppParameters'], posix=False)

        for arg in details['exec']['argv']:
            if arg.lower().startswith('--authrpc.jwtsecret'):
                details['is_merge_configured'] = True
                break
        
        if details['is_merge_configured'] == UNKNOWN_VALUE:
            details['is_merge_configured'] = False

        return details

    else:
        log.error(f'Unknown execution client {execution_client}.')
        return False

def get_geth_installed_version(base_directory):
    # Get the installed version for Geth

    log.info('Getting Geth installed version...')

    geth_path = base_directory.joinpath('bin', 'geth.exe')

    process_result = subprocess.run([geth_path, 'version'], capture_output=True,
        text=True)
    
    if process_result.returncode != 0:
        log.error(f'Unexpected return code from geth. Return code: '
            f'{process_result.returncode}')
        return UNKNOWN_VALUE
    
    process_output = process_result.stdout
    result = re.search(r'Version: (?P<version>[^-]+)', process_output)
    if not result:
        log.error(f'Cannot parse {process_output} for Geth installed version.')
        return UNKNOWN_VALUE
    
    installed_version = result.group('version')

    log.info(f'Geth installed version is {installed_version}')

    return installed_version

def get_consensus_client_details(base_directory, consensus_client):
    # Get the details for the current consensus client

    base_directory = Path(base_directory)

    nssm_binary = get_nssm_binary()
    if not nssm_binary:
        return False

    if consensus_client == CONSENSUS_CLIENT_TEKU:

        details = {
            'unified_service': True,
            'bn_service': {
                'found': False,
                'status': UNKNOWN_VALUE,
                'binary': UNKNOWN_VALUE,
                'parameters': UNKNOWN_VALUE,
                'running': UNKNOWN_VALUE
            },
            'vc_service': {
                'found': False,
                'status': UNKNOWN_VALUE,
                'binary': UNKNOWN_VALUE,
                'parameters': UNKNOWN_VALUE,
                'running': UNKNOWN_VALUE
            },
            'versions': {
                'installed': UNKNOWN_VALUE,
                'running': UNKNOWN_VALUE,
                'latest': UNKNOWN_VALUE
            },
            'bn_exec': {
                'path': UNKNOWN_VALUE,
                'argv': []
            },
            'vc_exec': {
                'path': UNKNOWN_VALUE,
                'argv': []
            },
            'is_bn_merge_configured': UNKNOWN_VALUE,
            'is_vc_merge_configured': UNKNOWN_VALUE
        }
        
        # Check for existing service
        teku_service_exists = False
        teku_service_name = 'teku'

        service_details = get_service_details(nssm_binary, teku_service_name)

        if service_details is not None:
            teku_service_exists = True
        
        if not teku_service_exists:
            return details

        details['bn_service']['found'] = True
        details['bn_service']['status'] = service_details['status']
        details['bn_service']['binary'] = service_details['install']
        details['bn_service']['parameters'] = service_details['parameters']['AppParameters']
        details['bn_service']['running'] = is_service_running(service_details)

        details['bn_exec']['path'] = service_details['install']
        details['bn_exec']['argv'] = shlex.split(service_details['parameters']['AppParameters'], posix=False)

        details['vc_service']['found'] = details['bn_service']['found']
        details['vc_service']['status'] = details['bn_service']['status']
        details['vc_service']['binary'] = details['bn_service']['binary']
        details['vc_service']['parameters'] = details['bn_service']['parameters']
        details['vc_service']['running'] = details['bn_service']['running']

        details['vc_exec']['path'] = details['bn_exec']['path']
        details['vc_exec']['argv'] = details['bn_exec']['argv']

        execution_jwt_flag_found = False
        execution_endpoint_flag_found = False
        for arg in details['bn_exec']['argv']:
            if arg.lower().startswith('--ee-jwt-secret-file'):
                execution_jwt_flag_found = True
            if arg.lower().startswith('--ee-endpoint'):
                execution_endpoint_flag_found = True
            if execution_jwt_flag_found and execution_endpoint_flag_found:
                break
        
        details['is_bn_merge_configured'] = (
            execution_jwt_flag_found and execution_endpoint_flag_found)
        
        for arg in details['vc_exec']['argv']:
            if arg.lower().startswith('--validators-proposer-default-fee-recipient'):
                details['is_vc_merge_configured'] = True
                break
        
        if details['is_vc_merge_configured'] == UNKNOWN_VALUE:
            details['is_vc_merge_configured'] = False

        details['versions']['installed'] = get_teku_installed_version(base_directory)
        details['versions']['running'] = get_teku_running_version()
        details['versions']['latest'] = get_teku_latest_version()

        return details

    else:
        log.error(f'Unknown consensus client {consensus_client}.')
        return False

def get_teku_installed_version(base_directory):
    # Get the installed version for Teku

    log.info('Getting Teku installed version...')

    teku_path = base_directory.joinpath('bin', 'teku')
    teku_batch_file = teku_path.joinpath('bin', 'teku.bat')

    teku_found = False
    teku_version = UNKNOWN_VALUE

    java_home = base_directory.joinpath('bin', 'jre')

    if teku_batch_file.is_file():
        try:
            env = os.environ.copy()
            env['JAVA_HOME'] = str(java_home)

            process_result = subprocess.run([
                str(teku_batch_file), '--version'
                ], capture_output=True, text=True, env=env)
            
            if process_result.returncode != 0:
                log.error(f'Unexpected return code from Teku. Return code: '
                    f'{process_result.returncode}')
                return UNKNOWN_VALUE

            teku_found = True

            process_output = process_result.stdout
            result = re.search(r'teku/(?P<version>[^/]+)', process_output)
            if result:
                teku_version = result.group('version').strip()
            else:
                log.error(f'We could not parse Teku version from output: {process_result.stdout}')

        except FileNotFoundError:
            pass

    if teku_found:
        log.info(f'Teku installed version is {teku_version}')

        return teku_version
    
    return UNKNOWN_VALUE

def get_teku_running_version():
    # Get the running version for Teku

    log.info('Getting Teku running version...')

    local_teku_bn_version_url = 'http://127.0.0.1:5051' + BN_VERSION_EP

    try:
        response = httpx.get(local_teku_bn_version_url)
    except httpx.RequestError as exception:
        log.error(f'Cannot connect to Teku. Exception: {exception}')
        return UNKNOWN_VALUE

    if response.status_code != 200:
        log.error(f'Unexpected status code from {local_teku_bn_version_url}. Status code: '
            f'{response.status_code}')
        return UNKNOWN_VALUE
    
    response_json = response.json()

    if 'data' not in response_json or 'version' not in response_json['data']:
        log.error(f'Unexpected JSON response from {local_teku_bn_version_url}. result not found.')
        return UNKNOWN_VALUE
    
    version_agent = response_json['data']['version']

    # Version agent should look like: teku/v22.8.1/windows-x86_64/-eclipseadoptium-openjdk64bitservervm-java-17
    result = re.search(r'teku/v(?P<version>[^-/]+)(-(?P<commit>[^-/]+))?',
        version_agent)
    if not result:
        log.error(f'Cannot parse {version_agent} for Teku version.')
        return UNKNOWN_VALUE

    running_version = result.group('version')

    log.info(f'Teku running version is {running_version}')

    return running_version

def get_teku_latest_version():
    # Get the latest version for Teku

    log.info('Getting Teku latest version...')

    teku_gh_release_url = GITHUB_REST_API_URL + TEKU_LATEST_RELEASE
    headers = {'Accept': GITHUB_API_VERSION}
    try:
        response = httpx.get(teku_gh_release_url, headers=headers,
            follow_redirects=True)
    except httpx.RequestError as exception:
        log.error(f'Exception while getting the latest stable version for Teku. {exception}')
        return UNKNOWN_VALUE

    if response.status_code != 200:
        log.error(f'HTTP error while getting the latest stable version for Teku. '
            f'Status code {response.status_code}')
        return UNKNOWN_VALUE
    
    release_json = response.json()

    if 'tag_name' not in release_json or not isinstance(release_json['tag_name'], str):
        log.error(f'Unable to find tag name in Github response while getting the latest stable '
            f'version for Teku.')
        return UNKNOWN_VALUE
    
    tag_name = release_json['tag_name']
    result = re.search(r'v?(?P<version>.+)', tag_name)
    if not result:
        log.error(f'Cannot parse tag name {tag_name} for Teku version.')
        return UNKNOWN_VALUE
    
    latest_version = result.group('version')

    log.info(f'Teku latest version is {latest_version}')

    return latest_version

def use_default_client(context):
    # Set the default clients in context if they are not provided

    selected_execution_client = CTX_SELECTED_EXECUTION_CLIENT
    selected_consensus_client = CTX_SELECTED_CONSENSUS_CLIENT

    updated_context = False

    if selected_execution_client not in context:
        context[selected_execution_client] = EXECUTION_CLIENT_GETH
        updated_context = True
    
    if selected_consensus_client not in context:
        context[selected_consensus_client] = CONSENSUS_CLIENT_TEKU
        updated_context = True

    if updated_context:
        if not save_state(WIZARD_COMPLETED_STEP_ID, context):
            return None

    return context

def perform_maintenance(base_directory, execution_client, execution_client_details,
    consensus_client, consensus_client_details):
    # Perform all the maintenance tasks

    base_directory = Path(base_directory)

    nssm_binary = get_nssm_binary()
    if not nssm_binary:
        return False

    if execution_client == EXECUTION_CLIENT_GETH:
        # Geth maintenance tasks
        geth_service_name = 'geth'

        if execution_client_details['next_step'] == MAINTENANCE_RESTART_SERVICE:
            log.info('Restarting Geth service...')

            subprocess.run([str(nssm_binary), 'restart', geth_service_name])

        elif execution_client_details['next_step'] == MAINTENANCE_UPGRADE_CLIENT:
            if not upgrade_geth(base_directory, nssm_binary):
                log.error('We could not upgrade the Geth client.')
                return False
        
        elif execution_client_details['next_step'] == MAINTENANCE_UPGRADE_CLIENT_MERGE:
            if not config_geth_merge(base_directory, nssm_binary):
                log.error('We could not configure Geth for the merge.')
                return False
            
            if not upgrade_geth(base_directory, nssm_binary):
                log.error('We could not upgrade the Geth client.')
                return False
    
        elif execution_client_details['next_step'] == MAINTENANCE_CONFIG_CLIENT_MERGE:
            if not config_geth_merge(base_directory, nssm_binary):
                log.error('We could not configure Geth for the merge.')
                return False
            
            log.info('Restarting Geth service...')

            subprocess.run([str(nssm_binary), 'restart', geth_service_name])

        elif execution_client_details['next_step'] == MAINTENANCE_START_SERVICE:
            log.info('Starting Geth service...')

            subprocess.run([str(nssm_binary), 'start', geth_service_name])

        elif execution_client_details['next_step'] == MAINTENANCE_REINSTALL_CLIENT:
            log.warn('TODO: Reinstalling client is to be implemented.')
    else:
        log.error(f'Unknown execution client {execution_client}.')
        return False
    
    if consensus_client == CONSENSUS_CLIENT_TEKU:
        # Teku maintenance tasks
        teku_service_name = 'teku'

        if consensus_client_details['next_step'] == MAINTENANCE_RESTART_SERVICE:
            log.info('Restarting Teku service...')

            subprocess.run([str(nssm_binary), 'restart', teku_service_name])

        elif consensus_client_details['next_step'] == MAINTENANCE_UPGRADE_CLIENT:
            if not upgrade_teku(base_directory, nssm_binary):
                log.error('We could not upgrade the Teku client.')
                return False
        
        elif consensus_client_details['next_step'] == MAINTENANCE_UPGRADE_CLIENT_MERGE:
            if not config_teku_merge(base_directory, nssm_binary):
                log.error('We could not configure Teku for the merge.')
                return False

            if not upgrade_teku(base_directory, nssm_binary):
                log.error('We could not upgrade the Teku client.')
                return False
    
        elif consensus_client_details['next_step'] == MAINTENANCE_CONFIG_CLIENT_MERGE:
            if not config_teku_merge(base_directory, nssm_binary):
                log.error('We could not configure Teku for the merge.')
                return False
            
            log.info('Restarting Teku service...')

            subprocess.run([str(nssm_binary), 'restart', teku_service_name])
            
        elif consensus_client_details['next_step'] == MAINTENANCE_START_SERVICE:
            log.info('Starting Teku service...')

            subprocess.run([str(nssm_binary), 'start', teku_service_name])

        elif consensus_client_details['next_step'] == MAINTENANCE_REINSTALL_CLIENT:
            log.warn('TODO: Reinstalling client is to be implemented.')
    else:
        log.error(f'Unknown consensus client {consensus_client}.')
        return False

    return True

def upgrade_geth(base_directory, nssm_binary):
    # Upgrade the Geth client
    log.info('Upgrading Geth client...')

    # Get list of geth releases/builds from their store
    next_marker = None
    page_end_found = False

    windows_builds = []

    try:
        log.info('Getting geth builds...')
        while not page_end_found:
            params = GETH_STORE_BUILDS_PARAMS.copy()
            if next_marker is not None:
                params['marker'] = next_marker

            response = httpx.get(GETH_STORE_BUILDS_URL, params=params, follow_redirects=True)

            if response.status_code != 200:
                log.error(f'Cannot connect to geth builds URL {GETH_STORE_BUILDS_URL}.\n'
                    f'Unexpected status code {response.status_code}')
                return False
            
            builds_tree_root = ElementTree.fromstring(response.text)
            blobs = builds_tree_root.findall('.//Blobs/Blob')

            for blob in blobs:
                build_name = blob.find('Name').text.strip()
                if build_name.endswith('.asc'):
                    continue

                if not is_stable_windows_amd64_archive(build_name):
                    continue

                build_properties = blob.find('Properties')
                last_modified_date = dateparse(build_properties.find('Last-Modified').text)

                windows_builds.append({
                    'name': build_name,
                    'last_modified_date': last_modified_date
                })

            next_marker = builds_tree_root.find('.//NextMarker').text
            if next_marker is None:
                page_end_found = True

    except httpx.RequestError as exception:
        log.error(f'Cannot connect to geth builds URL {GETH_STORE_BUILDS_URL}.\n'
            f'Exception {exception}')
        return False

    if len(windows_builds) <= 0:
        log.error('No geth builds found on geth store. We cannot continue.')
        return False

    # Download latest geth build and its signature
    windows_builds.sort(key=lambda x: (x['last_modified_date'], x['name']), reverse=True)
    latest_build = windows_builds[0]

    download_path = base_directory.joinpath('downloads')
    download_path.mkdir(parents=True, exist_ok=True)

    geth_archive_path = download_path.joinpath(latest_build['name'])
    if geth_archive_path.is_file():
        geth_archive_path.unlink()

    latest_build_url = urljoin(GETH_BUILDS_BASE_URL, latest_build['name'])

    try:
        with open(geth_archive_path, 'wb') as binary_file:
            log.info(f'Downloading geth archive {latest_build["name"]}...')
            with httpx.stream('GET', latest_build_url, follow_redirects=True) as http_stream:
                if http_stream.status_code != 200:
                    log.error(f'Cannot download geth archive {latest_build_url}.\n'
                        f'Unexpected status code {http_stream.status_code}')
                    return False
                for data in http_stream.iter_bytes():
                    binary_file.write(data)
    except httpx.RequestError as exception:
        log.error(f'Exception while downloading geth archive. Exception {exception}')
        return False

    geth_archive_sig_path = download_path.joinpath(latest_build['name'] + '.asc')
    if geth_archive_sig_path.is_file():
        geth_archive_sig_path.unlink()

    latest_build_sig_url = urljoin(GETH_BUILDS_BASE_URL, latest_build['name'] + '.asc')

    try:
        with open(geth_archive_sig_path, 'wb') as binary_file:
            log.info(f'Downloading geth archive signature {latest_build["name"]}.asc...')
            with httpx.stream('GET', latest_build_sig_url,
                follow_redirects=True) as http_stream:
                if http_stream.status_code != 200:
                    log.error(f'Cannot download geth archive signature {latest_build_sig_url}.\n'
                        f'Unexpected status code {http_stream.status_code}')
                    return False
                for data in http_stream.iter_bytes():
                    binary_file.write(data)
    except httpx.RequestError as exception:
        log.error(f'Exception while downloading geth archive signature. Exception {exception}')
        return False

    if not install_gpg(base_directory):
        return False
    
    # Verify PGP signature
    gpg_binary_path = base_directory.joinpath('bin', 'gpg.exe')

    command_line = [str(gpg_binary_path), '--list-keys', '--with-colons', GETH_WINDOWS_PGP_KEY_ID]
    process_result = subprocess.run(command_line)
    pgp_key_found = process_result.returncode == 0

    if not pgp_key_found:

        retry_index = 0
        retry_count = 15

        key_server = PGP_KEY_SERVERS[retry_index % len(PGP_KEY_SERVERS)]
        log.info(f'Downloading Geth Windows Builder PGP key from {key_server} ...')
        command_line = [str(gpg_binary_path), '--keyserver', key_server,
            '--recv-keys', GETH_WINDOWS_PGP_KEY_ID]
        process_result = subprocess.run(command_line)

        if process_result.returncode != 0:
            # GPG failed to download Geth Windows Builder PGP key, let's wait and retry a few times
            while process_result.returncode != 0 and retry_index < retry_count:
                retry_index = retry_index + 1
                delay = 5
                log.warning(f'GPG failed to download the PGP key. We will wait {delay} seconds '
                    f'and try again from a different server.')
                time.sleep(delay)

                key_server = PGP_KEY_SERVERS[retry_index % len(PGP_KEY_SERVERS)]
                log.info(f'Downloading Geth Windows Builder PGP key from {key_server} ...')
                command_line = [str(gpg_binary_path), '--keyserver', key_server,
                    '--recv-keys', GETH_WINDOWS_PGP_KEY_ID]

                process_result = subprocess.run(command_line)
        
        if process_result.returncode != 0:
            log.error(
f'''
We failed to download the Geth Windows Builder PGP key to verify the geth
archive after {retry_count} retries.
'''
            )
            return False
    
    process_result = subprocess.run([
        str(gpg_binary_path), '--verify', str(geth_archive_sig_path)])
    if process_result.returncode != 0:
        log.error('The geth archive signature is wrong. We\'ll stop here to protect you.')
        return False
    
    # Remove download leftovers
    geth_archive_sig_path.unlink()        

    # Unzip geth archive
    bin_path = base_directory.joinpath('bin')
    bin_path.mkdir(parents=True, exist_ok=True)

    geth_extracted_binary = None

    with ZipFile(geth_archive_path, 'r') as zip_file:
        for name in zip_file.namelist():
            if name.endswith('geth.exe'):
                geth_extracted_binary = Path(zip_file.extract(name, download_path))
    
    # Remove download leftovers
    geth_archive_path.unlink()

    if geth_extracted_binary is None:
        log.error('The geth binary was not found in the archive. We cannot continue.')
        return False

    # Move geth back into bin directory
    target_geth_binary_path = bin_path.joinpath('geth.exe')
    if target_geth_binary_path.is_file():
        target_geth_binary_path.unlink()
    
    geth_extracted_binary.rename(target_geth_binary_path)

    geth_extracted_binary.parent.rmdir()

    log.info('Restarting Geth service...')
    geth_service_name = 'geth'
    subprocess.run([str(nssm_binary), 'restart', geth_service_name])

    return True

def config_geth_merge(base_directory, nssm_binary):
    # Configure Geth for the merge
    log.info('Configuring Geth for the merge...')
    # TODO: Implemention

    """log.info('Creating JWT token file if needed...')
    if not setup_jwt_token_file():
        log.error(
f'''
Unable to create JWT token file in {LINUX_JWT_TOKEN_FILE_PATH}
'''
        )

        return False
    
    geth_service_name = GETH_SYSTEMD_SERVICE_NAME
    geth_service_content = ''

    log.info('Adding JWT token configuration to Geth...')

    with open('/etc/systemd/system/' + geth_service_name, 'r') as service_file:
        geth_service_content = service_file.read()

    result = re.search(r'ExecStart\s*=\s*(.*?)geth([^\\\n]*(\\\s+)?)*', geth_service_content)
    if not result:
        log.error('Cannot parse Geth service file.')
        return False
    
    exec_start = result.group(0)

    # Add --authrpc.jwtsecret configuration
    exec_start = re.sub(r'(\s*\\)?\s+--authrpc.jwtsecret\s*=?\s*\S+', '', exec_start)
    exec_start = exec_start + f' --authrpc.jwtsecret {LINUX_JWT_TOKEN_FILE_PATH}'

    geth_service_content = re.sub(r'ExecStart\s*=\s*(.*?)geth([^\\\n]*(\\\s+)?)*',
        exec_start, geth_service_content)

    # Write back configuration
    with open('/etc/systemd/system/' + geth_service_name, 'w') as service_file:
        service_file.write(geth_service_content)

    # Reload configuration
    log.info('Reloading service configurations...')
    subprocess.run(['systemctl', 'daemon-reload'])

    return True"""
    return False

def upgrade_teku(base_directory, nssm_binary):
    # Upgrade the Teku client
    log.info('Upgrading Teku client...')
    # TODO: Implemention

    """# Getting latest Teku release files
    teku_gh_release_url = GITHUB_REST_API_URL + TEKU_LATEST_RELEASE
    headers = {'Accept': GITHUB_API_VERSION}
    try:
        response = httpx.get(teku_gh_release_url, headers=headers,
            follow_redirects=True)
    except httpx.RequestError as exception:
        log.error(f'Exception while downloading teku binary. {exception}')
        return False

    if response.status_code != 200:
        log.error(f'HTTP error while downloading teku binary. '
            f'Status code {response.status_code}')
        return False
    
    release_json = response.json()

    if 'assets' not in release_json:
        log.error('No assets in Github release for teku.')
        return False
    
    binary_asset = None
    signature_asset = None

    archive_filename_comp = 'x86_64-unknown-linux-gnu.tar.gz'

    use_optimized_binary = is_adx_supported()
    if not use_optimized_binary:
        log.warn('CPU does not support ADX instructions. '
            'Using the portable version for Teku.')
        archive_filename_comp = 'x86_64-unknown-linux-gnu-portable.tar.gz'
    
    archive_filename_sig_comp = archive_filename_comp + '.asc'

    for asset in release_json['assets']:
        if 'name' not in asset:
            continue
        if 'browser_download_url' not in asset:
            continue

        file_name = asset['name']
        file_url = asset['browser_download_url']

        if file_name.endswith(archive_filename_comp):
            binary_asset = {
                'file_name': file_name,
                'file_url': file_url
            }
        elif file_name.endswith(archive_filename_sig_comp):
            signature_asset = {
                'file_name': file_name,
                'file_url': file_url
            }

    if binary_asset is None or signature_asset is None:
        log.error('Could not find binary or signature asset in Github release.')
        return False
    
    # Downloading latest Teku release files
    download_path = Path(Path.home(), 'ethwizard', 'downloads')
    download_path.mkdir(parents=True, exist_ok=True)

    binary_path = Path(download_path, binary_asset['file_name'])

    try:
        with open(binary_path, 'wb') as binary_file:
            with httpx.stream('GET', binary_asset['file_url'],
                follow_redirects=True) as http_stream:
                if http_stream.status_code != 200:
                    log.error(f'HTTP error while downloading Teku binary from Github. '
                        f'Status code {http_stream.status_code}')
                    return False
                for data in http_stream.iter_bytes():
                    binary_file.write(data)
    except httpx.RequestError as exception:
        log.error(f'Exception while downloading Teku binary from Github. {exception}')
        return False
    
    signature_path = Path(download_path, signature_asset['file_name'])

    try:
        with open(signature_path, 'wb') as signature_file:
            with httpx.stream('GET', signature_asset['file_url'],
                follow_redirects=True) as http_stream:
                if http_stream.status_code != 200:
                    log.error(f'HTTP error while downloading Teku signature from Github. '
                        f'Status code {http_stream.status_code}')
                    return False
                for data in http_stream.iter_bytes():
                    signature_file.write(data)
    except httpx.RequestError as exception:
        log.error(f'Exception while downloading Teku signature from Github. {exception}')
        return False

    # Test if gpg is already installed
    gpg_is_installed = False
    try:
        gpg_is_installed = is_package_installed('gpg')
    except Exception:
        return False

    if not gpg_is_installed:
        # Install gpg using APT
        subprocess.run([
            'apt', '-y', 'update'])
        subprocess.run([
            'apt', '-y', 'install', 'gpg'])

    # Verify PGP signature

    command_line = ['gpg', '--list-keys', '--with-colons', LIGHTHOUSE_PRIME_PGP_KEY_ID]
    process_result = subprocess.run(command_line)
    pgp_key_found = process_result.returncode == 0

    if not pgp_key_found:
        retry_index = 0
        retry_count = 15

        key_server = PGP_KEY_SERVERS[retry_index % len(PGP_KEY_SERVERS)]
        log.info(f'Downloading Sigma Prime\'s PGP key from {key_server} ...')
        command_line = ['gpg', '--keyserver', key_server, '--recv-keys',
            LIGHTHOUSE_PRIME_PGP_KEY_ID]
        process_result = subprocess.run(command_line)

        if process_result.returncode != 0:
            # GPG failed to download Sigma Prime's PGP key, let's wait and retry a few times
            while process_result.returncode != 0 and retry_index < retry_count:
                retry_index = retry_index + 1
                delay = 5
                log.warning(f'GPG failed to download the PGP key. We will wait {delay} seconds '
                    f'and try again from a different server.')
                time.sleep(delay)

                key_server = PGP_KEY_SERVERS[retry_index % len(PGP_KEY_SERVERS)]
                log.info(f'Downloading Sigma Prime\'s PGP key from {key_server} ...')
                command_line = ['gpg', '--keyserver', key_server, '--recv-keys',
                    LIGHTHOUSE_PRIME_PGP_KEY_ID]

                process_result = subprocess.run(command_line)
        
        if process_result.returncode != 0:
            log.error(
f'''
We failed to download the Sigma Prime's PGP key to verify the teku
binary after {retry_count} retries.
'''
            )
            return False
    
    process_result = subprocess.run([
        'gpg', '--verify', signature_path])
    if process_result.returncode != 0:
        log.error('The teku binary signature is wrong. '
            'We will stop here to protect you.')
        return False
    
    # Stopping Teku services before updating the binary
    log.info('Stopping Teku services...')
    subprocess.run(['systemctl', 'stop', LIGHTHOUSE_BN_SYSTEMD_SERVICE_NAME,
        LIGHTHOUSE_VC_SYSTEMD_SERVICE_NAME])

    # Extracting the Teku binary archive
    log.info('Updating Teku binary...')
    subprocess.run([
        'tar', 'xvf', binary_path, '--directory', LIGHTHOUSE_INSTALLED_DIRECTORY])
    
    # Restarting Teku services after updating the binary
    log.info('Starting Teku services...')
    subprocess.run(['systemctl', 'start', LIGHTHOUSE_BN_SYSTEMD_SERVICE_NAME,
        LIGHTHOUSE_VC_SYSTEMD_SERVICE_NAME])

    # Remove download leftovers
    binary_path.unlink()
    signature_path.unlink()

    return True"""
    return False

def config_teku_merge(base_directory, nssm_binary):
    # Configure Teku for the merge
    log.info('Configuring Teku for the merge...')
    # TODO: Implemention

    """fee_recipient_address = select_fee_recipient_address()
    if not fee_recipient_address:
        log.error('No fee recipient address entered.')
        return False

    log.info('Creating JWT token file if needed...')
    if not setup_jwt_token_file():
        log.error(
f'''
Unable to create JWT token file in {LINUX_JWT_TOKEN_FILE_PATH}
'''
        )

        return False
    
    # Configure the Teku beacon node

    teku_bn_service_name = LIGHTHOUSE_BN_SYSTEMD_SERVICE_NAME
    teku_bn_service_content = ''

    log.info('Adding JWT token configuration to Teku beacon node and '
        'using the correct API port...')

    with open('/etc/systemd/system/' + teku_bn_service_name, 'r') as service_file:
        teku_bn_service_content = service_file.read()

    result = re.search(r'ExecStart\s*=\s*(.*?)teku([^\\\n]*(\\\s+)?)*', teku_bn_service_content)
    if not result:
        log.error('Cannot parse Teku beacon node service file.')
        return False
    
    exec_start = result.group(0)

    # Remove all --eth1-endpoints related configuration
    exec_start = re.sub(r'(\s*\\)?\s+--eth1-endpoints?\s*=?\s*\S+', '', exec_start)

    # Add --execution-endpoint configuration
    exec_start = re.sub(r'(\s*\\)?\s+--execution-endpoints?\s*=?\s*\S+', '', exec_start)
    exec_start = exec_start + ' --execution-endpoint http://127.0.0.1:8551'

    # Add --execution-jwt configuration
    exec_start = re.sub(r'(\s*\\)?\s+--execution-jwt\s*=?\s*\S+', '', exec_start)
    exec_start = exec_start + f' --execution-jwt {LINUX_JWT_TOKEN_FILE_PATH}'

    teku_bn_service_content = re.sub(r'ExecStart\s*=\s*(.*?)teku([^\\\n]*(\\\s+)?)*',
        exec_start, teku_bn_service_content)

    # Write back configuration
    with open('/etc/systemd/system/' + teku_bn_service_name, 'w') as service_file:
        service_file.write(teku_bn_service_content)

    # Configure the Teku validator client

    teku_vc_service_name = LIGHTHOUSE_VC_SYSTEMD_SERVICE_NAME
    teku_vc_service_content = ''

    with open('/etc/systemd/system/' + teku_vc_service_name, 'r') as service_file:
        teku_vc_service_content = service_file.read()
    
    result = re.search(r'ExecStart\s*=\s*(.*?)teku([^\\\n]*(\\\s+)?)*', teku_vc_service_content)
    if not result:
        log.error('Cannot parse Teku validator client service file.')
        return False

    exec_start = result.group(0)

    # Add fee recipient address
    exec_start = re.sub(r'(\s*\\)?\s+--suggested-fee-recipient\s*=?\s*\S+', '', exec_start)
    exec_start = exec_start + f' --suggested-fee-recipient {fee_recipient_address}'
    
    teku_vc_service_content = re.sub(r'ExecStart\s*=\s*(.*?)teku([^\\\n]*(\\\s+)?)*',
        exec_start, teku_vc_service_content)

    # Write back configuration
    with open('/etc/systemd/system/' + teku_vc_service_name, 'w') as service_file:
        service_file.write(teku_vc_service_content)

    # Reload configuration
    log.info('Reloading service configurations...')
    subprocess.run(['systemctl', 'daemon-reload'])

    return True"""
    return False