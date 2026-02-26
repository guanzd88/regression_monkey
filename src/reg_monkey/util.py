import json,os
class ConfigLoader:
    def __init__(self, project_path=os.getcwd()):
        """初始化配置加载器，自动加载指定配置文件。

        :param config_file: 配置文件的名称，默认为 config.json
        """
        config_path = os.path.join(project_path, 'config.json')
        # plan_path = os.path.join(project_path, 'task_plan.json')
        self.load_config(config_path)
        # self.load_task_plan(plan_path)
    
    def load_config(self, path):
        """从指定路径加载配置文件并将其内容设置为类属性。
        :param path: 配置文件的完整路径
        """
        try:
            with open(path, 'r') as file:
                config = json.load(file)
                for key, value in config.items():
                    setattr(self, key, value)
        except FileNotFoundError:
            print(f"Configuration file {path} was not found.")
        except json.JSONDecodeError:
            print(f"Configuration file {path} is malformed.")

    def get_config(self):
        """返回当前加载的配置字典。"""
        return self.config
    
class PublicConfig:
    '''
    这个类用于声明一些公共的配置和关联管理，方便进行管理。
    '''
    TEMPLATE_MAP: dict = {
        # 语言键统一为小写
        "r": "r_template.jinja",
    }

    MODEL_MAPPING = {
        # 'OLS':'',
        'FE': 'plm',
        'FMB': 'fmb_regression',
    }

    LIBRARY_REQUIREMENTS = {
        "r": {
            # 回归/建模
            "OLS": [],          # lm(), summary()
            "FE": ["plm"],             # plm(..., model="within")
            "RE": ["plm"],             # plm(..., model="random")
            "FMB": [],                 # fmb_two_pass relies on base stats only

            # 预处理/数据结构
            "winsorize": ["DescTools"],    # DescTools::Winsorize
            "set_panel": ["plm"],          # pdata.frame()
            "clean_sample": [],            # base/Stats 完成

            # 模板辅助宏（不需要外部依赖）
            "set_category_controls": [],   # as.factor()
            "extract_coefficients": [],    # 基础数据框操作
            "install_required_packages": []# install.packages()/library()
        },
        "stata": {
            "OLS": [],
            "FE": [],
            "RE": [],
            "IV2SLS": [],   # Stata 自带 ivregress
            "PSM": [],      # Stata 自带 teffects psmatch
            "HECKMAN": [],  # Stata 自带 heckman
        },
    }

import re

def name_bracket_bidir(s: str) -> str:
    """
    双向转换：
    1) 原始格式:  <name>[<a>,<b>]
       - name: 只能字母/数字/下划线，任意长度
       - a: 整数（可负/0/正），b: 非负整数
       - 校验：a <= 0 <= b
       正向编码为:
         a<0  => <name>__<abs(a)>_<b>_
         a>=0 => <name>_<a>_<b>_
    2) 编码格式:
         <name>__<abs(a)>_<b>_  (表示原始 a 为负)
         <name>_<a>_<b>_        (表示原始 a 为非负)
       逆向还原为 <name>[<a>,<b>]
    """

    if not isinstance(s, str) or s == "":
        raise ValueError(f"Input must be a non-empty string, got: {type(s)}")

    # ---- 原始格式：<name>[a,b] ----
    raw_pat = re.compile(r"^([A-Za-z0-9_]+)\[\s*([+-]?\d+)\s*,\s*(\d+)\s*\]$")
    m = raw_pat.match(s)
    if m:
        name = m.group(1)
        a = int(m.group(2))
        b = int(m.group(3))

        # 左大右小（按你示例实现为：左<=0<=右）
        if not (a <= 0 <= b):
            raise ValueError(f"Invalid index order (expected left<=0<=right): {s}")

        if a < 0:
            return f"{name}__{abs(a)}_{b}_"
        else:
            return f"{name}_{a}_{b}_"

    # ---- 编码格式：<name>__abs(a)_b_ 或 <name>_a_b_ ----
    enc_pat = re.compile(r"^([A-Za-z0-9_]+)(_ {1,2})(\d+)_(\d+)_$".replace(" ", ""))
    m = enc_pat.match(s)
    if m:
        name = m.group(1)
        sign_token = m.group(2)  # "_" or "__"
        a_mag = int(m.group(3))
        b = int(m.group(4))

        a = -a_mag if sign_token == "__" else a_mag

        if not (a <= 0 <= b):
            raise ValueError(f"Invalid encoded index order (expected left<=0<=right): {s}")

        return f"{name}[{a},{b}]"

    return s
