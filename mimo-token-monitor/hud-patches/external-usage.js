import * as fs from 'node:fs';
const MAX_BALANCE_LABEL_LENGTH = 50;
function parseUsagePercent(value) {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        return null;
    }
    return Math.round(Math.min(100, Math.max(0, value)));
}
function sanitizeBalanceLabel(value) {
    if (typeof value !== 'string') {
        return null;
    }
    const sanitized = value
        .replace(/\x1B\[[0-?]*[ -/]*[@-~]/g, '')
        .replace(/\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)/g, '')
        .replace(/\x1B[@-Z\\-_]/g, '')
        .replace(/[\u0000-\u001F\u007F-\u009F]/g, '')
        .replace(/[\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069\u206A-\u206F]/g, '')
        .trim();
    if (!sanitized) {
        return null;
    }
    if (sanitized.length <= MAX_BALANCE_LABEL_LENGTH) {
        return sanitized;
    }
    return `${sanitized.slice(0, MAX_BALANCE_LABEL_LENGTH - 3)}...`;
}
function parseDateValue(value) {
    if (typeof value === 'number') {
        if (!Number.isFinite(value) || value <= 0) {
            return null;
        }
        const millis = value > 1e12 ? value : value * 1000;
        const date = new Date(millis);
        return Number.isNaN(date.getTime()) ? null : date;
    }
    if (typeof value === 'string' && value.trim()) {
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? null : date;
    }
    return null;
}
function parseUpdatedAt(value) {
    const date = parseDateValue(value);
    return date ? date.getTime() : null;
}
export function getUsageFromExternalSnapshot(config, now = Date.now()) {
    const snapshotPath = config.display.externalUsagePath;
    if (!snapshotPath) {
        return null;
    }
    try {
        const raw = fs.readFileSync(snapshotPath, 'utf8');
        const parsed = JSON.parse(raw);
        const updatedAt = parseUpdatedAt(parsed.updated_at);
        if (updatedAt === null) {
            return null;
        }
        // Staleness check removed — rely on triggerExternalSync to keep data fresh.
        // This ensures data is always displayed even if the sync command fails.
        const fiveHour = parseUsagePercent(parsed.five_hour?.used_percentage);
        const sevenDay = parseUsagePercent(parsed.seven_day?.used_percentage);
        const balanceLabel = sanitizeBalanceLabel(parsed.balance_label);
        if (fiveHour === null && sevenDay === null && balanceLabel === null) {
            return null;
        }
        const fiveHourResetAt = parseDateValue(parsed.five_hour?.resets_at);
        const sevenDayResetAt = parseDateValue(parsed.seven_day?.resets_at);
        if (parsed.five_hour && parsed.five_hour.resets_at != null && fiveHourResetAt === null) {
            return null;
        }
        if (parsed.seven_day && parsed.seven_day.resets_at != null && sevenDayResetAt === null) {
            return null;
        }
        const usage = {
            fiveHour,
            sevenDay,
            fiveHourResetAt,
            sevenDayResetAt,
        };
        if (balanceLabel !== null) {
            usage.balanceLabel = balanceLabel;
        }
        return usage;
    }
    catch {
        return null;
    }
}
//# sourceMappingURL=external-usage.js.map
