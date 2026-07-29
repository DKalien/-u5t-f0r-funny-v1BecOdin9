# Task 8 回归覆盖报告

## 状态
完成。已补充同步生命周期、失败降级和重复启动回归覆盖；未发现需要生产代码调整的真实接缝问题。

## Commit
- 代码与测试提交：`c99731d test(data-sync): 覆盖同步失败与窗口生命周期`
- 本次 EOF 格式修复提交：见最终 commit

## 测试总数
- `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v`
- 44 tests，全部 PASS
- `python -m compileall -q .` PASS

## 覆盖内容
- 启动 pull 失败仍创建并显示窗口
- 首次配置取消安全返回且不创建窗口
- 重复实例在构造同步服务、启动同步前返回
- `closeEvent` 仅隐藏到托盘，不触发 push
- 真正退出 callback 幂等，仅请求一次
- 远端目标缺失保留本地数据库
- push/fetch 失败保留本地数据库
- 默认 `unittest discover -s tests` 发现全部测试

## TDD
先添加并运行新增回归测试；生命周期测试在现有实现上直接通过，说明 Task 1-7 的接口已经满足要求。数据同步新增场景首次运行暴露了测试 fixture 在删除远端目标后本地数据库也被删除的问题，修正 fixture 顺序后通过。未添加不必要的生产代码。

## 修复的既有问题
未复现 `ResourceWarning`；已有 `fdopen` 失败资源关闭和临时文件清理测试均通过，因此无需额外修复连接关闭逻辑。

## 自审
变更仅限：
- `mimo-token-monitor/tests/test_sync_runtime.py`
- `mimo-token-monitor/tests/test_data_sync.py`

测试使用 offscreen Qt、fake service 和 tempfile Git fixture，无真实数据目录、GitHub 或网络访问。
