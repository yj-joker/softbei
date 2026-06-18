package ai.weixiu.common;

public class RedisKey {
        public static final String USER_SESSION_ID="User:SessionId:";
        public static final String USER_EMAIL_CODE="User:Email:Code:";
        @Deprecated
        public static final String PREFERENCE_CACHE = "Memory:Preference:";
        /** 用户级偏好缓存（不含sessionId，变更时一次清除） */
        public static final String PREFERENCE_CACHE_USER = "Memory:Preference:User:";
        /** 会话级偏好缓存（含sessionId，仅当前会话有效） */
        public static final String PREFERENCE_CACHE_SESSION = "Memory:Preference:Session:";
        public static final String CONSOLIDATION_LOCK = "Memory:Consolidation:Lock:";

        /**
         * 阅读会话缓存前缀。
         * <p>完整 key 后拼接 readSessionId，Hash 中保存 userId、manualId 和最近一次心跳时间。</p>
         */
        public static final String MANUAL_READ_SESSION = "Maintenance:Manual:Read:Session:";

        /**
         * 阅读会话心跳锁前缀。
         * <p>用于串行处理同一个 readSessionId 的心跳，避免同一段阅读时长被重复累计。</p>
         */
        public static final String MANUAL_READ_SESSION_LOCK = "Maintenance:Manual:Read:Session:Lock:";

        /**
         * 用户当天阅读某本手册的累计时长前缀。
         * <p>完整 key 会包含 userId、manualId 和业务日期，单位为秒。</p>
         */
        public static final String MANUAL_READ_DURATION = "Maintenance:Manual:Read:Duration:";

        /**
         * 用户已为某本手册计入排行榜的标记前缀（终身一次）。
         * <p>该标记通过 setIfAbsent 抢占，保证同一用户对同一手册只为排行榜增加一次分值。</p>
         */
        public static final String MANUAL_READ_COUNTED = "Maintenance:Manual:Read:Counted:";

        /** 日榜 ZSet 前缀，完整 key 追加 yyyyMMdd。 */
        public static final String MANUAL_RANK_DAY = "Maintenance:Manual:Rank:Day:";

        /** 周榜 ZSet 前缀，完整 key 追加 ISO 周年和周序号。 */
        public static final String MANUAL_RANK_WEEK = "Maintenance:Manual:Rank:Week:";

        /** 月榜 ZSet 前缀，完整 key 追加 yyyyMM。 */
        public static final String MANUAL_RANK_MONTH = "Maintenance:Manual:Rank:Month:";

        /** 总榜 ZSet key，长期累计有效阅读次数。 */
        public static final String MANUAL_RANK_TOTAL = "Maintenance:Manual:Rank:Total";

        /** 个性化推荐缓存前缀，完整 key 追加 userId。TTL 2小时。 */
        public static final String MANUAL_RECOMMEND = "Recommend:Manual:";
}
