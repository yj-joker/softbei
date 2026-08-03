package ai.weixiu.config;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ServiceTokenContractValidatorTest {

    private static final String INTERNAL_SECRET = "internal-test-secret";
    private static final String API_SECRET = "api-test-secret";

    @ParameterizedTest
    @NullSource
    @ValueSource(strings = {"", "   ", " "})
    void rejectsMissingInternalToken(String internalToken) {
        assertThatThrownBy(() -> ServiceTokenContractValidator.validate(internalToken, API_SECRET))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("internal")
                .hasMessageNotContaining(API_SECRET);
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(strings = {"", "   ", " "})
    void rejectsMissingApiToken(String apiToken) {
        assertThatThrownBy(() -> ServiceTokenContractValidator.validate(INTERNAL_SECRET, apiToken))
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
