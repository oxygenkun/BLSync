from .configs import load_configs

# 延迟加载配置，避免在导入时执行
global_configs = None


def get_global_configs():
    global global_configs
    if global_configs is None:
        global_configs = load_configs()
    return global_configs
