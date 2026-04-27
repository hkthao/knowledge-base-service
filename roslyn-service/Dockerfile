FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY roslyn-service.csproj .
RUN dotnet restore
COPY . .
RUN dotnet publish -c Release -o /app /p:UseAppHost=false

# Final stage also uses SDK because MSBuildWorkspace needs the SDK to load
# .csproj files (the aspnet runtime image is not enough). Image is ~900MB —
# unavoidable trade-off for full Roslyn semantic analysis.
FROM mcr.microsoft.com/dotnet/sdk:8.0
WORKDIR /app
COPY --from=build /app .

EXPOSE 5000
ENV ASPNETCORE_URLS=http://0.0.0.0:5000 \
    DOTNET_RUNNING_IN_CONTAINER=true
ENTRYPOINT ["dotnet", "roslyn-service.dll"]
