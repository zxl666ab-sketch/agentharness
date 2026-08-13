FROM node:22-alpine AS web-build
WORKDIR /workspace/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM eclipse-temurin:21-jdk AS java-build
WORKDIR /workspace/procurement-service
COPY procurement-service/ ./
COPY --from=web-build /workspace/web/dist ./src/main/resources/static
RUN chmod +x mvnw \
    && MVNW_REPOURL=https://maven.aliyun.com/repository/public ./mvnw -q -DskipTests package

FROM eclipse-temurin:21-jre
WORKDIR /app
RUN useradd --system --uid 10001 caijiatai \
    && mkdir -p /data/artifacts \
    && chown -R caijiatai:caijiatai /data /app
COPY --from=java-build /workspace/procurement-service/target/procurement-service-0.5.0.jar /app/procurement-service.jar
USER 10001
EXPOSE 8741
ENTRYPOINT ["java", "-jar", "/app/procurement-service.jar"]
