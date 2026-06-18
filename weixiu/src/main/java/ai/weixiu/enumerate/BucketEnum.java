package ai.weixiu.enumerate;

import lombok.Getter;

@Getter
public enum BucketEnum {

    // 公开桶 —— 头像、展示图（桶策略设为 public）
    PUBLIC("weixiu-public-tupian"),

    // 私有桶 —— 文档、报修附件、报告（桶策略保持 private）
    PRIVATE("weixiu-private-wendang");

    private final String name;

    BucketEnum(String name) {
        this.name = name;
    }

}