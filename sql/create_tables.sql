-- Account Info Table
CREATE TABLE account_info (
    account_id SERIAL PRIMARY KEY, -- 帳戶的唯一識別碼
    account_name VARCHAR(255) NOT NULL, -- 帳戶名
    api_key VARCHAR(255), -- API密鑰
    api_secret VARCHAR(255), -- API秘密
    other_info TEXT, -- 其他帳戶相關信息
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP, -- 記錄創建時間
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP -- 記錄最後更新時間
);

COMMENT ON COLUMN account_info.account_id IS '帳戶的唯一識別碼';
COMMENT ON COLUMN account_info.account_name IS '帳戶名';
COMMENT ON COLUMN account_info.api_key IS 'API密鑰';
COMMENT ON COLUMN account_info.api_secret IS 'API秘密';
COMMENT ON COLUMN account_info.other_info IS '其他帳戶相關信息';
COMMENT ON COLUMN account_info.created_at IS '記錄創建時間';
COMMENT ON COLUMN account_info.updated_at IS '記錄最後更新時間';

-- Strategies Table
CREATE TABLE strategies (
    strategy_id SERIAL PRIMARY KEY, -- 策略的唯一識別碼
    account_id INT REFERENCES account_info(account_id), -- 關聯的帳戶ID
    strategy_name VARCHAR(255) NOT NULL, -- 策略的名稱
    initial_capital NUMERIC(10, 2) NOT NULL, -- 實行策略時的初始資本
    risk_parameters TEXT, -- 風險控制參數
    entry_criteria TEXT, -- 進場標準
    exit_criteria TEXT, -- 出場標準
    status VARCHAR(50), -- 策略的當前狀態
    passphrase VARCHAR(36), -- 用於接收 Trading View 訊號的 UUID
    trade_group_id varchar(36) NULL, -- 交易群組ID
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP, -- 紀錄創建時間
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP -- 紀錄最後更新時間
);

COMMENT ON COLUMN strategies.strategy_id IS '策略的唯一識別碼';
COMMENT ON COLUMN strategies.account_id IS '關聯的帳戶ID';
COMMENT ON COLUMN strategies.strategy_name IS '策略的名稱';
COMMENT ON COLUMN strategies.initial_capital IS '實行策略時的初始資本';
COMMENT ON COLUMN strategies.risk_parameters IS '風險控制參數';
COMMENT ON COLUMN strategies.entry_criteria IS '進場標準';
COMMENT ON COLUMN strategies.exit_criteria IS '出場標準';
COMMENT ON COLUMN strategies.status IS '策略的當前狀態';
COMMENT ON COLUMN strategies.passphrase IS '用於接收 Trading View 訊號的 UUID';
COMMENT ON COLUMN strategies.trade_group_id IS '交易群組ID';
COMMENT ON COLUMN strategies.created_at IS '紀錄創建時間';
COMMENT ON COLUMN strategies.updated_at IS '紀錄最後更新時間';

-- Trades Table
CREATE TABLE trades (
    trade_id SERIAL PRIMARY KEY, -- 交易的唯一識別碼
    thirdparty_id BIGINT, -- 第三方ID，用於存儲大整數
    strategy_id INT NOT NULL REFERENCES strategies(strategy_id), -- 關聯的策略ID
    symbol VARCHAR(20), -- 交易標的符號
    trade_type VARCHAR(4), -- 交易類型
    quantity NUMERIC(10, 2), -- 交易數量
    price NUMERIC(10, 2), -- 交易價格
    profit_loss NUMERIC(10, 2) DEFAULT 0, -- 盈虧
    cumulative_profit_loss NUMERIC(10, 2) DEFAULT 0, -- 累計盈虧
    trade_group_id varchar(36) NULL, -- 交易群組ID
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP, -- 創建時間
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP -- 更新時間
);

COMMENT ON COLUMN trades.trade_id IS '交易的唯一識別碼';
COMMENT ON COLUMN trades.thirdparty_id IS '第三方ID，用於存儲大整數';
COMMENT ON COLUMN trades.strategy_id IS '關聯的策略ID';
COMMENT ON COLUMN trades.symbol IS '交易標的符號';
COMMENT ON COLUMN trades.trade_type IS '交易類型';
COMMENT ON COLUMN trades.quantity IS '交易數量';
COMMENT ON COLUMN trades.price IS '交易價格';
COMMENT ON COLUMN trades.profit_loss IS '盈虧';
COMMENT ON COLUMN trades.cumulative_profit_loss IS '累計盈虧';
COMMENT ON COLUMN trades.trade_group_id IS '交易群組ID';
COMMENT ON COLUMN trades.created_at IS '創建時間';
COMMENT ON COLUMN trades.updated_at IS '更新時間';

-- Account Balance Table
CREATE TABLE account_balance (
    balance_id SERIAL PRIMARY KEY, -- 帳戶餘額的唯一識別碼
    strategy_id INT NOT NULL REFERENCES strategies(strategy_id), -- 關聯的策略ID
    balance NUMERIC(10, 2) NOT NULL, -- 當前餘額
    equity NUMERIC(10, 2) NOT NULL, -- 淨值
    available_margin NUMERIC(10, 2) NOT NULL, -- 可用保證金
    used_margin NUMERIC(10, 2) NOT NULL, -- 使用中的保證金
    profit_loss NUMERIC(10, 2) DEFAULT 0, -- 實時盈虧
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP, -- 紀錄創建時間
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP -- 紀錄最後更新時間
);

COMMENT ON COLUMN account_balance.balance_id IS '帳戶餘額的唯一識別碼';
COMMENT ON COLUMN account_balance.strategy_id IS '關聯的策略ID';
COMMENT ON COLUMN account_balance.balance IS '當前餘額';
COMMENT ON COLUMN account_balance.equity IS '淨值';
COMMENT ON COLUMN account_balance.available_margin IS '可用保證金';
COMMENT ON COLUMN account_balance.used_margin IS '使用中的保證金';
COMMENT ON COLUMN account_balance.profit_loss IS '實時盈虧';
COMMENT ON COLUMN account_balance.created_at IS '紀錄創建時間';
COMMENT ON COLUMN account_balance.updated_at IS '紀錄最後更新時間';

-- Trigger Function for updating 'updated_at' column
CREATE OR REPLACE FUNCTION trigger_update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP; -- 在更新時自動設置當前時間戳
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Triggers for updating 'updated_at' in each table
CREATE TRIGGER update_account_info_modtime BEFORE UPDATE ON account_info FOR EACH ROW EXECUTE FUNCTION trigger_update_timestamp();
CREATE TRIGGER update_strategies_modtime BEFORE UPDATE ON strategies FOR EACH ROW EXECUTE FUNCTION trigger_update_timestamp();
CREATE TRIGGER update_trades_modtime BEFORE UPDATE ON trades FOR EACH ROW EXECUTE FUNCTION trigger_update_timestamp();
CREATE TRIGGER update_account_balance_modtime BEFORE UPDATE ON account_balance FOR EACH ROW EXECUTE FUNCTION trigger_update_timestamp();


-- ORDER_EXECUTIONS
-- 建立 order_executions 表
CREATE TABLE public.order_executions (
    execution_id serial8 NOT NULL,
    strategy_id int4 NOT NULL,
    binance_execution_id varchar(50) NOT NULL UNIQUE,
    execution_type varchar(10) NOT NULL CHECK (execution_type IN ('PARTIAL', 'FULL')),
    symbol varchar(20) NOT NULL,
    order_id varchar(50) NOT NULL,
    client_order_id varchar(50) NOT NULL,
    side varchar(10) NOT NULL,
    price numeric(20, 8) NOT NULL,
    quantity numeric(20, 8) NOT NULL,
    commission numeric(20, 8) NOT NULL,
    commission_asset varchar(10) NOT NULL,
    realized_pnl numeric(20, 8) NOT NULL,
    execution_time timestamp NOT NULL,
    created_at timestamp DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp DEFAULT CURRENT_TIMESTAMP,
    memo varchar NULL,

    CONSTRAINT order_executions_pkey PRIMARY KEY (execution_id),
    CONSTRAINT order_executions_strategy_id_fkey
        FOREIGN KEY (strategy_id)
        REFERENCES public.strategies(strategy_id)
);

-- 建立索引
CREATE INDEX idx_order_executions_strategy_symbol_time
    ON public.order_executions (strategy_id, symbol, execution_time);

CREATE INDEX idx_order_executions_order_id
    ON public.order_executions (order_id);

CREATE INDEX idx_order_executions_client_order_id
    ON public.order_executions (client_order_id);

CREATE INDEX idx_order_executions_execution_time_brin
    ON public.order_executions USING BRIN (execution_time);

-- 建立更新時間戳的觸發器
CREATE TRIGGER update_order_executions_modtime
    BEFORE UPDATE ON public.order_executions
    FOR EACH ROW
    EXECUTE FUNCTION trigger_update_timestamp();

-- 建立註解
COMMENT ON TABLE public.order_executions IS '訂單執行記錄表';
COMMENT ON COLUMN public.order_executions.binance_execution_id IS '幣安成交ID';
COMMENT ON COLUMN public.order_executions.execution_type IS '執行類型 (PARTIAL=部分成交, FULL=完全成交)';
COMMENT ON COLUMN public.order_executions.price IS '成交價格';
COMMENT ON COLUMN public.order_executions.quantity IS '本次成交數量';
COMMENT ON COLUMN public.order_executions.commission IS '手續費';
COMMENT ON COLUMN public.order_executions.commission_asset IS '手續費資產類型';
COMMENT ON COLUMN public.order_executions.realized_pnl IS '已實現盈虧';