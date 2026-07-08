package ai.weixiu.enumerate;

import lombok.Getter;
import lombok.RequiredArgsConstructor;

import java.util.Arrays;

@Getter
@RequiredArgsConstructor
public enum TaskVoiceAction {
    ANSWER_QUESTION("answer_question", "回答问题"),
    COMPLETE_CURRENT_STEP("complete_current_step", "完成当前步骤"),
    GO_NEXT_STEP("go_next_step", "进入下一步"),
    GO_PREV_STEP("go_prev_step", "回到上一步"),
    JUMP_TO_STEP("jump_to_step", "跳转到指定步骤"),
    REPEAT_CURRENT_STEP("repeat_current_step", "重复当前步骤"),
    CONFIRM_OVERRIDE("confirm_override", "确认强制完成"),
    ADD_STEP_NOTE("add_step_note", "补充备注"),
    REQUEST_PHOTO("request_photo", "请求拍照/上传照片"),
    CONFIRM_CHECKPOINT("confirm_checkpoint", "确认检查点"),
    UNDO_STEP_COMPLETION("undo_step_completion", "撤销上次完成"),
    EXIT_VOICE_MODE("exit_voice_mode", "退出语音模式"),
    CLARIFY("clarify", "需要澄清"),
    NO_OP("no_op", "无有效操作");

    private final String value;
    private final String label;

    public static TaskVoiceAction fromValue(String value) {
        if (value == null || value.isBlank()) {
            return NO_OP;
        }
        return Arrays.stream(values())
                .filter(action -> action.value.equals(value))
                .findFirst()
                .orElse(NO_OP);
    }
}

