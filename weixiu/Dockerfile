# ===== 构建阶段：Maven + JDK21 打包 =====
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build

# 先拷 pom 预热依赖缓存（pom 不变时这层可命中缓存，加速后续构建）
COPY pom.xml .
RUN mvn -q -B dependency:go-offline

# 再拷源码打包（跳过测试，部署环境通常无测试用中间件）
COPY src ./src
RUN mvn -q -B clean package -DskipTests

# ===== 运行阶段：精简 JRE21 =====
FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /build/target/*.jar app.jar
EXPOSE 8080

# 时区 + 容器感知的内存限制
ENV TZ=Asia/Shanghai \
    JAVA_OPTS="-XX:MaxRAMPercentage=75.0"
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
