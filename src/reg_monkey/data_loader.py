import os

class DataLoader:
    """
    轻量数据加载/清洗基类。

    用途
    ----
    以“一个 DataLoader 管一个产物（symbol/pkl）”为约定，封装从本地或远端读取原始数据、
    进行最小必要清洗，并将结果写回到 `self.df`（以及可选持久化为 PKL）。该基类仅
    约定接口与最小状态，不绑定具体存储或框架，便于在调度器/管理器中被动态导入与调用。

    参数
    ----
    output_pkl_name : str | None
        期望产出的 PKL 文件名或符号名；上层（如 DataManager）可据此决定持久化路径或
        ArcticDB 的 symbol 名。为 None 时由上层决定。
    dependency : Any | None
        依赖声明（如其他 symbol 列表、上游文件名、配置对象等）。基类不做语义约束，
        仅保存以供上层解析依赖图或传递上下文。

    主要属性
    --------
    df : pandas.DataFrame | None
        清洗后的数据表。`clean_data()` 应该赋值于此（或返回该表）。
    cwd : str
        实例化时的当前工作目录（`os.getcwd()`），便于构造相对路径。
    output_pkl_name : str | None
        期望产出名（见上）。
    dependency : Any | None
        依赖声明（见上）。

    典型用法
    --------
    1) 继承本类并重写 `clean_data()`，在其中读取原始数据→清洗→返回 DataFrame（或赋给 `self.df`）。
    2) 上层调度器创建实例并调用 `clean_data()`，随后可选择将 `self.df` 持久化。

    示例
    ----
    >>> class PricesLoader(DataLoader):
    ...     def clean_data(self):
    ...         raw = pd.read_csv("source_data/prices.csv")
    ...         df = (raw
    ...               .assign(date=lambda x: pd.to_datetime(x["date"]))
    ...               .sort_values(["symbol","date"])
    ...               .dropna(subset=["price"]))
    ...         self.df = df
    ...         return df
    """
    def __init__(self, output_pkl_name=None, dependency=None):
        self.df = None
        self.cwd = os.getcwd()
        self.output_pkl_name = output_pkl_name
        self.dependency = dependency

    def clean_data(self):
        """
        子类中重写。默认返回 None 表示无需清洗，仅加载原始数据。
        """
        return None