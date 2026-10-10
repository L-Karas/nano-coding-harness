# 工具就是它的类

## Status

accepted

工具的唯一参数声明是它的 schema 字段，唯一执行入口是类上的 `run` / `arun` 方法：执行器用 `cls.model_validate(args)` 完成校验并得到实例，方法体读 `self.*`；`arun` 未实现时缺省调用 `run`。自由 `run_*` / `run_*_async` 函数与名字约定不再存在。

## Considered Options

- **实例方法**（采纳）：schema 即参数表，校验即实例化，一个 module 同时拥有接口与实现。
- **classmethod**（拒绝）：handler 住进类里，但方法签名与字段仍重复一份。
- **类内挂载自由函数**（拒绝）：只是把两份声明绑在一起，没有消除名字约定。
