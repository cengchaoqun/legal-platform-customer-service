-- 法律咨询平台客服：Customer 数据库增量迁移
-- 适用版本：从电商客服基线（conversations / messages / conversation_turns / handoffs / realtime_outbox）
-- 迁移原则：只新增列与表，不删除旧数据，不重建消息历史。
-- 执行：psql -d customer_service -f 002_legal_flow.sql

BEGIN;

-- 1. Turn 需要记录领取时的流程版本，并限制同一输入的流程重算次数
ALTER TABLE conversation_turns
    ADD COLUMN IF NOT EXISTS base_flow_state_version INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS flow_recompute_count INTEGER NOT NULL DEFAULT 0;

-- 2. 工单增加转人工原因、队列与结构化交接上下文
ALTER TABLE handoffs
    ADD COLUMN IF NOT EXISTS reason_code VARCHAR(40),
    ADD COLUMN IF NOT EXISTS queue_code VARCHAR(40) NOT NULL DEFAULT 'platform_aftersales',
    ADD COLUMN IF NOT EXISTS context_snapshot JSONB,
    ADD COLUMN IF NOT EXISTS resolution_context JSONB;

-- 3. 跨轮流程状态：每个会话一行，state_version 单调递增
CREATE TABLE IF NOT EXISTS conversation_flow_states (
    conversation_id VARCHAR(80) PRIMARY KEY REFERENCES conversations(id),
    flow_id VARCHAR(80) NOT NULL,
    flow_type VARCHAR(40),
    node VARCHAR(40) NOT NULL DEFAULT 'INIT',
    slots JSONB NOT NULL DEFAULT '{}'::jsonb,
    pending_intents JSONB NOT NULL DEFAULT '[]'::jsonb,
    clarification_attempts JSONB NOT NULL DEFAULT '{}'::jsonb,
    state_version INTEGER NOT NULL DEFAULT 0,
    flow_definition_version VARCHAR(60) NOT NULL DEFAULT 'legal-support-flow-v1',
    last_applied_turn_id VARCHAR(80),
    handoff_checkpoint JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_flow_states_flow_type ON conversation_flow_states (flow_type);

-- 4. 同一 Agent Run 只允许一条最终回复，避免重放产生重复消息
--    （基线已有 uq_agent_run_outcome_message，这里仅兜底确认）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_agent_run_outcome_message'
    ) THEN
        ALTER TABLE messages
            ADD CONSTRAINT uq_agent_run_outcome_message UNIQUE (agent_run_id, agent_outcome_seq);
    END IF;
END $$;

COMMIT;
