package ai.weixiu.service;

/**
 * 语音合成（TTS）服务。
 *
 * <p>把文字交给阿里云百炼 DashScope CosyVoice 合成为 mp3 音频字节，供前端 {@code <audio>} 播放。
 * 与现有 ASR（{@code AsrStreamHandler}）同一服务商、同一 key、同一 SDK，方向相反。</p>
 */
public interface TtsService {

    /**
     * 同步合成一段语音。
     *
     * @param text  要朗读的文字（非空）
     * @param voice 音色，传 null/空 用默认音色 longxiaochun（龙小淳）
     * @return mp3 音频字节（MP3_22050HZ_MONO_16BIT）
     */
    byte[] synthesize(String text, String voice);
}
