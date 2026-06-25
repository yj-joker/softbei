package ai.weixiu.entity;

import lombok.Data;

/**
 * 语音合成请求体：{@code POST /weixiu/ai/tts} 入参。
 */
@Data
public class TtsRequest {

    /** 要朗读的文字。 */
    private String text;

    /** 音色（可选），不传用默认 longxiaochun（龙小淳）。 */
    private String voice;
}
