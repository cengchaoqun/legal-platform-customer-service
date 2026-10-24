# 法律平台演示业务服务

> 这是**演示服务**，用于在没有真实法律平台接口时提供可重复的业务事实（套餐、订单、办理进度、发票、售后）。
> 它不连接真实支付渠道，不执行资金退款，也不代表任何真实平台的处理结果。

## 职责

- 对 AI Service 提供只读查询接口，事实由当前登录用户身份限定归属；
- 对用户前端提供写接口：取消订单、申请开票、申请售后；
- 所有写操作要求 `Idempotency-Key`，相同键 + 相同请求体返回原结果，相同键 + 不同请求体返回 `409 IDEMPOTENCY_CONFLICT`。

## 启动

```bash
cd 代码/legal-business-service
../../.venv/Scripts/python.exe -m atguigu_legal.main
```

默认端口 `8001`，数据库 `legal_business`。启动时自动执行 `create_all` 与种子数据导入（仅当表为空）。

## 与真实平台的边界

| 能力 | 演示实现 | 接真实平台时需要 |
|---|---|---|
| 身份 | `POST /api/v1/auth/demo-token` 直接签发 JWT | 替换为平台登录与用户体系 |
| 订单/套餐/进度 | PostgreSQL 种子数据 | 字段适配，不复制订单域 |
| 开票 | 生成 `SUBMITTED` 申请记录 | 调用平台开票接口并回读状态 |
| 退款 | 生成 `SUBMITTED` 售后申请，`refund_status=NOT_REFUNDED` | 平台审批与支付渠道退款 |
| 取消订单 | 状态置为 `CANCELLED`（不涉及资金） | 平台取消规则与退款联动 |
