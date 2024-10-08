import pathlib
import subprocess
import math
import time
import datetime
import os
import logging
from typing import Required
import colorlog
import signal
import shutil
import yaml
import tempfile
#configs
TARGETS_FILE="targets.yaml"
MAX_UPLOAD_SIZE_CONF= 30 #MB


MAX_UPLOAD_SIZE = MAX_UPLOAD_SIZE_CONF * 1024 * 1024

# Define the format and log colors
log_format = '%(asctime)s [%(levelname)s] %(name)s [%(funcName)s]: %(message)s'
log_colors = {
        'DEBUG': 'cyan',
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'bold_red',
        }

# Create the ColoredFormatter object
console_formatter = colorlog.ColoredFormatter(
        '%(log_color)s' + log_format,
        log_colors = log_colors
        )

stdout_handler=logging.StreamHandler()
stdout_handler.setFormatter(console_formatter)
stdout_handler.setLevel(logging.DEBUG)

logger = logging.getLogger()

logger.setLevel(logging.DEBUG)
logger.addHandler(stdout_handler)


GITHUB_SIZE_LIMIT= 1 * 1024 * 1024 * 100
FILE_NAME=datetime.datetime.fromtimestamp(time.time()).strftime("%d_%m_%Y:%H:%M")
OLD_PWD : pathlib.Path =pathlib.Path().resolve()
MAX_PUSH_ATTEMPTS=5

path_size_cache : dict = {}

#yaml config paring variables
CONF_REQ_FIELDS=["root", "repo"]
CONF_REQ_EITHER = [("targets", "no-target")] #every config must have either of each entry


CURRENT_ROOT="" #used by sig_int_handler()

def check_fields_log_n_exit_if_invalid(conf : dict):

    present_req_fields_in_conf = list (set(CONF_REQ_FIELDS) & set(conf.keys()))
    if sorted(CONF_REQ_FIELDS) != sorted(present_req_fields_in_conf):
        missing_fields = [field for field in CONF_REQ_FIELDS if field not in conf.keys()]
        raise Exception(1, f"missing required fields ({missing_fields}) in {conf}")

    for tuple_either_conf in CONF_REQ_EITHER:
        either_conf_found=False
        for either_conf in tuple_either_conf:
            if either_conf in conf.keys():
                either_conf_found = True
                break
        if not either_conf_found:
            raise Exception(2,f"either of these fields need to be provided ({tuple_either_conf}) in {conf}") 

def parse_config():
    read_buff = open(TARGETS_FILE).read()
    parsed_yaml_conf = yaml.safe_load(read_buff)
    for conf in parsed_yaml_conf:
        check_fields_log_n_exit_if_invalid(conf)

    return parsed_yaml_conf
        


def convert_size(size_bytes):
    #https://stackoverflow.com/questions/5194057/better-way-to-convert-file-sizes-in-python
   if size_bytes == 0:
       return "0B"
   size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
   i = int(math.floor(math.log(size_bytes, 1024)))
   p = math.pow(1024, i)
   s = round(size_bytes / p, 2)
   return "%s %s" % (s, size_name[i])


def size_to_byte(size):
    raise NotImplementedError


def size_of_path(path : pathlib.Path):

    try:
        res : int= path_size_cache[path]
        return res

    except KeyError:
        pass

    path=pathlib.Path(path)

    if path.is_file():
        res = path.stat().st_size
        path_size_cache.update({path : res})
        return res

    res = 0

    for subfile in list(path.glob("*")):
        if subfile.is_file():
            res += subfile.stat().st_size
            continue
        res += size_of_path(subfile)

    path_size_cache.update({path : res})
    return res 


def backup_init(repo):

    repo_name : str=repo.split("/")[-1]
    temp_dir = tempfile.TemporaryDirectory()
    did_backup_repo_name=False
    if os.path.exists(repo_name):
        did_backup_repo_name=True
        logger.debug(f"backing up {repo_name}")
        shutil.move(repo_name, temp_dir.name)

    p = subprocess.run(["git", "clone", "--no-checkout", "--depth", "1", repo])
    if p.returncode != 0:
        raise Exception(f"failed to {p.args}, err code : {p.returncode}")

    path_file_git=pathlib.Path(f"{repo_name}/.git").resolve()
    cwd = pathlib.Path(".").resolve()

    try:
        shutil.move(path_file_git, cwd)
    except shutil.Error as e:
        if "already exists" in e.args[0]:
            pass

    shutil.rmtree(repo_name)
    if did_backup_repo_name:
        shutil.move(f"{temp_dir.name}/{repo_name}",cwd)

    path_file_gitattr = pathlib.Path(f"{OLD_PWD}/.gitattributes").resolve()

    try:
        shutil.move(path_file_gitattr, cwd)
    except shutil.Error as e:
        print(e.args)
        if "already exists" in e.args[0]:
            pass
    except FileNotFoundError:
        pass

    try:
        subprocess.run(["git", "add", ".gitattributes"])
    except Exception:
        pass


def push_backup(path : pathlib.Path):

    size=convert_size(size_of_path(path))

    if size_of_path(path) > GITHUB_SIZE_LIMIT:
        logger.critical(f"{path} is bigger than githubs upload limit")
        return 
    logger.info(f"pushing '{path}', size: {size}")
    subprocess.run(["git", "add", str(path)])
    subprocess.run(["git", "commit", "-m", FILE_NAME],\
            stdout=subprocess.DEVNULL,\
            stderr=subprocess.DEVNULL)

    push_success=False
    push_attempts=0

    while push_success == False and push_attempts < MAX_PUSH_ATTEMPTS:
        if push_attempts:
            logger.warning(f"failed to push, attemp {push_attempts} of {MAX_PUSH_ATTEMPTS}")
        push_attempts += 1
        
        p_report=subprocess.run(["git", "push", "-f", "-u", "origin", "main"])
        if p_report.returncode == 0:
            push_success=True
        push_attempts += 1 

    if push_success == False:
        logger.error(f"failed to push {path}")
        

def push_backup_list(paths : list[pathlib.Path]):
    size = 0
    for path in paths:
        size += size_of_path(path)
    logger.info(f"pushing group {paths}, {convert_size(size)}")

    for path in paths:
        if size_of_path(path) > GITHUB_SIZE_LIMIT:
            logger.critical(f"{path} is bigger than githubs upload limit, skipping")
            continue
 
        subprocess.run(["git", "add", str(path)])
    subprocess.run(["git", "commit", "-m", FILE_NAME],\
            stdout=subprocess.DEVNULL,\
            stderr=subprocess.DEVNULL)


    push_success=False
    push_attempts=0

    while push_success == False and push_attempts < MAX_PUSH_ATTEMPTS:
        if push_attempts:
            logger.warning(f"failed to push, attemp {push_attempts} of {MAX_PUSH_ATTEMPTS}")
        push_attempts += 1
        
        p_report=subprocess.run(["git", "push", "-f", "-u", "origin", "main"])
        if p_report.returncode == 0:
            push_success=True
        push_attempts += 1 

    if push_success == False:
        logger.error(f"failed to push {paths}")
        


def backup_wrapup():
    subprocess.run(["rm", "-rf", ".git"])
    subprocess.run(["rm", "-f", ".gitattributes"])

def optimized_backup_push(dirs : list[pathlib.Path])->int:
    push_list = [dirs[0]]
    current_push_list_size = size_of_path(dirs[0])
    for direc in dirs[1:]:
        if size_of_path(direc) + current_push_list_size < MAX_UPLOAD_SIZE:
            push_list.append(direc)
            current_push_list_size += size_of_path(direc)
    push_backup_list(push_list)
    return len(push_list)

def backup_dir(path: pathlib.Path):

    path=pathlib.Path(path)

    if pathlib.Path(path).is_file():
        push_backup(path)
        return

    if size_of_path(path) > MAX_UPLOAD_SIZE:
        children = list(path.glob("*"))

        if len(children) == 0:
            logger.critical(f"{path} doesnt have any children {children=}")
            return
        
        children.sort(key=size_of_path)
        number_of_pushed_childs=0
        if size_of_path(children[0]) < MAX_UPLOAD_SIZE:
            number_of_pushed_childs=optimized_backup_push(children)
        
        assert number_of_pushed_childs >= 0 and number_of_pushed_childs <= len(children)
        
        for child in children[number_of_pushed_childs:]:
            backup_dir(child)
        return

    push_backup(path)

def sig_int_handler(signal, frame):
    os.chdir(CURRENT_ROOT)
    backup_wrapup()



if __name__ == "__main__":
    signal.signal(signal.SIGINT, sig_int_handler)

    backup_confs = parse_config()
    logger.debug(f"loaded configs: {backup_confs}")
    for conf in backup_confs:
        CURRENT_ROOT= pathlib.Path(conf['root']).resolve()


        OLD_PWD=pathlib.Path().resolve()
        os.chdir(CURRENT_ROOT)
        backup_init(conf['repo'])

        if 'no-target' in conf.keys():
            backup_dir(pathlib.Path(".").resolve())
            continue

        for target in conf['targets']:
            backup_dir(pathlib.Path(target).resolve())
        
        assert pathlib.Path().resolve() == CURRENT_ROOT, f"expected cwd to be {CURRENT_ROOT} not pathlib.Path().resolve()"
        backup_wrapup()
        os.chdir(OLD_PWD)



