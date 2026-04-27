namespace RoslynService.Models;

public class AnalysisResult
{
    public List<EntityDto> Entities { get; set; } = new();
    public List<RelationDto> Relations { get; set; } = new();
}
