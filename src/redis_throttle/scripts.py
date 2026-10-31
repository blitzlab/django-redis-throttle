"""Lua scripts for each algorithm.

Every script runs atomically inside Redis, so concurrent requests from any
number of web workers can never both read the same state and both succeed.
Each algorithm keeps its state in a single key, which keeps the scripts
Redis Cluster safe (all keys are declared in KEYS and live in one slot).

Arguments (identical for every script):
    KEYS[1]  state key
    ARGV[1]  limit       maximum requests per window (bucket capacity)
    ARGV[2]  period_ms   window length in milliseconds
    ARGV[3]  cost        units to consume; 0 means "peek" and never writes
    ARGV[4]  now_ms      optional clock override for tests; empty uses Redis TIME

Return value (all integers):
    {allowed (1/0), remaining, reset_after_ms, retry_after_ms}

Using the Redis server clock instead of each web server's clock means a fleet
with skewed clocks still enforces one consistent limit.
"""

_NOW = """
local now = tonumber(ARGV[4])
if not now then
  local t = redis.call('TIME')
  now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end
"""

FIXED_WINDOW = (
    _NOW
    + """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local period = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])

local window = math.floor(now / period)
local reset_after = (window + 1) * period - now

local state = redis.call('HMGET', key, 'w', 'c')
local count = 0
if tonumber(state[1]) == window then
  count = tonumber(state[2]) or 0
end

if count + cost > limit then
  return {0, limit - count, reset_after, reset_after}
end
if cost == 0 then
  return {1, limit - count, reset_after, 0}
end

count = count + cost
redis.call('HSET', key, 'w', window, 'c', count)
redis.call('PEXPIRE', key, reset_after)
return {1, limit - count, reset_after, 0}
"""
)

# Sliding window counter: the estimate weights the previous window by how much
# of it still overlaps the sliding window. Two integers of state per key, and
# it smooths out the 2x burst a fixed window allows at window boundaries.
SLIDING_WINDOW = (
    _NOW
    + """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local period = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])

local window = math.floor(now / period)
local elapsed = now - window * period
local reset_after = period - elapsed

local state = redis.call('HMGET', key, 'w', 'c', 'p')
local stored_window = tonumber(state[1])
local current = tonumber(state[2]) or 0
local previous = tonumber(state[3]) or 0

if stored_window == nil then
  current, previous = 0, 0
elseif stored_window == window - 1 then
  previous, current = current, 0
elseif stored_window ~= window then
  current, previous = 0, 0
end

local estimated = previous * (period - elapsed) / period + current

-- The small epsilon absorbs float error when the estimate lands exactly on the limit.
if estimated + cost > limit + 1e-9 then
  local retry_after
  if previous > 0 and current + cost <= limit then
    -- Wait until enough of the previous window has slid out.
    retry_after = math.ceil(reset_after - (limit - current - cost) * period / previous)
  else
    -- The current window alone is over budget: wait for it to become the
    -- previous window and decay far enough.
    local into_next = 0
    if current > 0 then
      into_next = math.max(0, period - (limit - cost) * period / current)
    end
    retry_after = math.ceil(reset_after + into_next)
  end
  return {0, math.max(0, math.floor(limit - estimated)), reset_after, math.max(1, retry_after)}
end

if cost == 0 then
  return {1, math.floor(limit - estimated), reset_after, 0}
end

current = current + cost
redis.call('HSET', key, 'w', window, 'c', current, 'p', previous)
redis.call('PEXPIRE', key, reset_after + period)
return {1, math.max(0, math.floor(limit - estimated - cost)), reset_after, 0}
"""
)

# Token bucket, implemented as GCRA (generic cell rate algorithm). It behaves
# exactly like a bucket of `limit` tokens refilled at limit/period, but stores a
# single integer timestamp (the "theoretical arrival time") instead of a
# fractional token count, so there is no floating point drift in Redis.
TOKEN_BUCKET = (
    _NOW
    + """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local period = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local interval = period / limit  -- ms to earn back one token

local tat = tonumber(redis.call('GET', key))
if tat == nil or tat < now then
  tat = now
end

local new_tat = math.ceil(tat + cost * interval)
local allow_at = new_tat - period

if now < allow_at then
  return {0, math.max(0, math.floor((period - (tat - now)) / interval)), tat - now, allow_at - now}
end
if cost > 0 then
  redis.call('SET', key, new_tat, 'PX', new_tat - now)
end
return {1, math.floor((period - (new_tat - now)) / interval), new_tat - now, 0}
"""
)

SCRIPTS = {
    "fixed_window": FIXED_WINDOW,
    "sliding_window": SLIDING_WINDOW,
    "token_bucket": TOKEN_BUCKET,
}
