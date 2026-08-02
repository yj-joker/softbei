package ai.weixiu.config;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ServiceTokenContractValidatorTest {

    private static final String INTERNAL_SECRET = "internal-test-secret";
    private static final String API_SECRET = "api-test-secret";

    @Test
    void rejectsMissingInternalToken() {
        assertThatThrownBy(() -> ServiceTokenContractValidator.validate(null, API_SECRET))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("internal")
                .hasMessageNotContaining(API_SECRET);
        assertThatThrownBy(() -> ServiceTokenContractValidator.validate("   ", API_SECRET))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("internal")
                .hasMessageNotContaining(API_SECRET);
    }

    @Test
    void rejectsMissingApiToken() {
        assertThatThrownBy(() -> ServiceTokenContractValidator.validate(INTERNAL_SECRET, null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("API")
                .hasMessageNotContaining(INTERNAL_SECRET);
        assertThatThrownBy(() -> ServiceTokenContractValidator.validate(INTERNAL_SECRET, ""))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("API")
                .hasMessageNotContaining(INTERNAL_SECRET);
    }

    @Test
    void rejectsEqualTokensAfterTrimming() {
        assertThatThrownBy(() -> ServiceTokenContractValidator.validate(" shared-secret ", "shared-secret"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("相同")
                .hasMessageNotContaining("shared-secret");
    }

    @Test
    void allowsDistinctNonBlankTokens() {
        assertThatCode(() -> ServiceTokenContractValidator.validate(" internal-secret ", API_SECRET))
                .doesNotThrowAnyException();
    }
}
